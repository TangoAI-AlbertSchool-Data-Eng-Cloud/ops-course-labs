"""The world around the platform: customers, the people who answer them, and
the regulator's clock. A simulation, so that every SLI has events to count.

* Customers send complaints at RATE per second, each a real complaint from
  the dataset.
* Each team's agents pick up the complaints routed to them. A complaint that
  belongs to another team is SENT BACK to the dispatch desk; the right team
  asks the drafter for a first reply, then answers.
* The dispatch desk re-routes by hand what was sent back or left unassigned,
  at a limited pace: DESK_RATE complaints per second.
* The clock: every complaint must be answered within 15 business days. When
  its deadline arrives, it counts as met or breached, labelled by whether the
  law sets that deadline (payment products) or the company does (the rest).

Time is compressed: one business day is DAY_SECONDS (60 by default). How long
an agent takes follows the dataset: the complaints the real company answered
late are answered late here too.
"""

import json
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
from prometheus_client import Counter, Gauge, start_http_server

from opentelemetry import trace

from lab import telemetry
from lab.common import REDIS, fault_on
from lab.telemetry import log
from lab.data import PSD2_TEAMS, ROUTING, TEAMS, load_complaints

# All of these are set in compose.yaml.
GATEWAY = os.environ.get("GATEWAY_URL", "http://gateway:8000")
DRAFTER = os.environ.get("DRAFTER_URL", "http://drafter:8000")
RATE = float(os.environ.get("RATE", "2"))
DAY = float(os.environ.get("DAY_SECONDS", "60"))
DESK_RATE = float(os.environ.get("DESK_RATE", "0.2"))

SENT = Counter("world_complaints_sent_total", "Complaints sent to the gateway", ["outcome"])
SENT_BACK = Counter("routing_sent_back_total", "Complaints a team sent back: not theirs")
DESK_DONE = Counter("dispatch_rerouted_total", "Complaints the dispatch desk re-routed by hand")
CLOSED = Counter("complaints_answered_total", "Complaints answered", ["within_deadline", "response"])
DEADLINES = Counter("complaint_deadlines_total", "Deadlines reached, by result and by whether PSD2 sets them",
                    ["result", "psd2"])
DESK_DEPTH = Gauge("dispatch_queue_depth", "Complaints waiting at the dispatch desk")

rng = random.Random()        # unseeded: each run sends different complaints
complaints = load_complaints()
pool = ThreadPoolExecutor(max_workers=16)   # agents asking for drafts in parallel
tracer = telemetry.setup("world")


def in_complaint_trace(complaint_id: str):
    """The context to continue a complaint's trace in: the one the gateway
    stored with it (None if it has none, or has expired)."""
    return telemetry.context_from(REDIS.hget(f"complaint:{complaint_id}", "traceparent"))


def days(low: float, high: float) -> float:
    """A random duration between `low` and `high` business days, in seconds."""
    return rng.uniform(low, high) * DAY


def schedule(at: float, event: dict) -> None:
    """Something that will happen at time `at`: a pick-up, or an answer."""
    REDIS.zadd("events", {json.dumps(event): at})


def customers() -> None:
    """Send complaints, RATE per second on average, at random intervals."""
    with httpx.Client(timeout=5) as http:
        while True:
            c = rng.choice(complaints)
            try:
                reply = http.post(f"{GATEWAY}/complaints", json={
                    "customer_ref": f"C{rng.randrange(10**8):08d}",   # a pseudonym, never a name
                    "narrative": c["narrative"],
                })
                reply.raise_for_status()
                complaint_id = reply.json()["id"]
                # What the dataset says about this complaint, kept out of the
                # platform's reach: its real product, and whether the real
                # company answered it late.
                REDIS.set(f"truth:{complaint_id}", json.dumps({
                    "team": ROUTING[c["product"]], "late": c["timely_response"] == "No",
                    "response": c["company_response"] or "Closed"}), ex=3 * 3600)
                SENT.labels("accepted").inc()
            except httpx.HTTPError:
                SENT.labels("failed").inc()   # the gateway refused or did not answer
            time.sleep(rng.expovariate(RATE))


def answer_time(truth: dict) -> float:
    """How long the right agent takes: late complaints past the 15-day limit."""
    return days(16, 22) if truth["late"] else days(2, 10)


def teams() -> None:
    """Each team takes the complaints routed to it; the desk takes the rest."""
    while True:
        moved = 0
        for team in TEAMS:
            while (complaint_id := REDIS.lpop(f"queue:team:{team}")):
                # An agent opens it after a short while in the team's queue.
                schedule(time.time() + days(0.5, 2), {"kind": "pickup", "id": complaint_id, "team": team})
                moved += 1
        # Unassigned complaints, and those triage could not read, go to people.
        for queue in ("queue:team:unassigned", "queue:team:dispatch"):
            while (complaint_id := REDIS.lpop(queue)):
                REDIS.rpush("desk", complaint_id)
                moved += 1
        if not moved:
            time.sleep(0.5)


def pickup(complaint_id: str, team: str) -> None:
    """An agent opens the complaint: theirs, or not."""
    # The agent's work joins the complaint's trace, days (minutes) after it began.
    with tracer.start_as_current_span("agent opens complaint", context=in_complaint_trace(complaint_id),
                                      attributes={"complaint.id": complaint_id, "team": team}):
        _pickup(complaint_id, team)


def _pickup(complaint_id: str, team: str) -> None:
    truth = json.loads(REDIS.get(f"truth:{complaint_id}") or "null")
    if truth is None:
        return
    if team != truth["team"]:
        # Not this team's product: back to the dispatch desk, time lost.
        SENT_BACK.inc()
        REDIS.rpush("desk", complaint_id)
        log.info("complaint sent back", extra={"complaint_id": complaint_id, "team": team})
        return
    try:
        # The agent waits for a first draft (the platform's synchronous LLM call).
        httpx.post(f"{DRAFTER}/drafts", json={"complaint_id": complaint_id}, timeout=10)
    except httpx.HTTPError:
        pass   # the drafter counts its own failures; the agent writes from scratch
    schedule(time.time() + answer_time(truth), {"kind": "answer", "id": complaint_id})


def answer(complaint_id: str) -> None:
    """The complaint is answered: within its deadline, or not."""
    key = f"complaint:{complaint_id}"
    due = REDIS.hget(key, "due_at")
    truth = json.loads(REDIS.get(f"truth:{complaint_id}") or "null")
    if due is None or truth is None:
        return
    within = "yes" if time.time() <= float(due) else "no"
    REDIS.hset(key, mapping={"status": "answered", "answered_at": time.time(), "within_deadline": within})
    CLOSED.labels(within, truth["response"]).inc()
    with tracer.start_as_current_span("complaint answered", context=in_complaint_trace(complaint_id),
                                      attributes={"complaint.id": complaint_id, "within_deadline": within}):
        log.info("complaint answered", extra={"complaint_id": complaint_id, "within_deadline": within})
    # For the nightly report: what was answered, and when.
    REDIS.zadd("answered", {complaint_id: time.time()})
    REDIS.zremrangebyscore("answered", 0, time.time() - 3 * 3600)   # keep three hours


def events() -> None:
    """Carry out every scheduled pick-up and answer whose time has come."""
    while True:
        due = REDIS.zrangebyscore("events", 0, time.time(), start=0, num=500)
        for raw in due:
            if REDIS.zrem("events", raw):   # each event happens once
                e = json.loads(raw)
                if e["kind"] == "pickup":
                    pool.submit(pickup, e["id"], e["team"])
                else:
                    answer(e["id"])
        if not due:
            time.sleep(0.2)


def desk() -> None:
    """The dispatch desk: people re-route by hand, DESK_RATE per second."""
    allowance = 0.0
    while True:
        rate = DESK_RATE / 2 if fault_on("dispatch-short") else DESK_RATE
        allowance = min(allowance + rate * 0.5, 5)   # what half a second of work allows
        while allowance >= 1 and (complaint_id := REDIS.lpop("desk")):
            allowance -= 1
            truth = json.loads(REDIS.get(f"truth:{complaint_id}") or "null")
            if truth is None:
                continue
            DESK_DONE.inc()
            # A person reads it and sends it to the right team.
            REDIS.hset(f"complaint:{complaint_id}", "team", truth["team"])
            schedule(time.time() + days(0.5, 2), {"kind": "pickup", "id": complaint_id, "team": truth["team"]})
        DESK_DEPTH.set(REDIS.llen("desk"))
        time.sleep(0.5)


def clock() -> None:
    """When a complaint's deadline arrives, was it answered in time?"""
    while True:
        for complaint_id in REDIS.zrangebyscore("deadlines", 0, time.time(), start=0, num=1000):
            REDIS.zrem("deadlines", complaint_id)
            status = REDIS.hget(f"complaint:{complaint_id}", "status")
            truth = json.loads(REDIS.get(f"truth:{complaint_id}") or "null")
            if status is None or truth is None:
                continue   # expired: not counted
            # The law applies by the customer's real product, not by what
            # triage guessed: a card complaint sent nowhere is still late.
            psd2 = "yes" if truth["team"] in PSD2_TEAMS else "no"
            DEADLINES.labels("met" if status == "answered" else "breached", psd2).inc()
        time.sleep(1)


def main() -> None:
    start_http_server(9100)
    # Independent loops, one thread each.
    for target in (customers, teams, events, desk, clock):
        threading.Thread(target=target, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == "__main__":
    main()
