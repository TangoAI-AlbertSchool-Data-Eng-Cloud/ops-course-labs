"""Triage: the asynchronous worker that reads a complaint and routes it.

For each complaint on the queue it
1. asks the LLM to read the narrative: which product, which issue, is the
   customer vulnerable, is it urgent (the triage);
2. checks the answer (the validation);
3. looks the product up in the routing table and puts the complaint on that
   team's queue; a product the table does not know goes to "unassigned".

Nobody waits on it, so its health is measured by how fast complaints get
through, not by request latency.
"""

import json
import os
import threading
import time

from prometheus_client import Counter, Gauge, Histogram, start_http_server

from lab import llm, rules
from lab.common import REDIS, degradation_level, fault_on
from lab.data import PRODUCTS, PRODUCTS_2023, ROUTING

# Eight complaints in flight at once: each waits on the LLM most of the time.
WORKERS = int(os.environ.get("TRIAGE_WORKERS", "8"))
INVALID_RETRIES = 2        # extra attempts when the LLM's answer fails validation
# The longest narrative sent whole, about 2,000 tokens. Longer ones are cut,
# and counted: the model then reads only part of the complaint.
MAX_CHARS = 8000

# What the SLIs are computed from. Labels hold a handful of fixed values each.
# Per attempt as well as per complaint: with retries, a malformed answer
# rarely costs a complaint, but every attempt is paid for.
ATTEMPTS = Counter("triage_attempts_total", "LLM triage attempts, retries included", ["result"])
OUTCOMES = Counter("triage_outcomes_total", "Complaints leaving triage, by where they went", ["outcome"])
ROUTED_TO = Counter("triage_routed_total", "Complaints routed, by team", ["team"])
TRUNCATED = Counter("narratives_truncated_total", "Narratives too long to send whole")
TRIAGE_TIME = Histogram("complaint_triage_seconds", "From receipt to routing",
                        buckets=(1, 2, 5, 10, 20, 30, 60, 120, 300))
QUEUE_DEPTH = Gauge("triage_queue_depth", "Complaints waiting for triage")
LEVEL = Gauge("degradation_level", "The degradation level the platform runs at")


def _prompt(names: list[str]) -> str:
    """The triage instructions, listing the product names to choose from."""
    options = "; ".join(f'"{p}"' for p in names)
    return ("TRIAGE. You read complaints written by customers of a consumer-finance company.\n"
            "Return only a JSON object, with no other text, holding these keys:\n"
            f'- "product": exactly one of {options}\n'
            '- "issue": the problem, in a few words\n'
            '- "vulnerable": true if the customer appears to be elderly, a service member, ill, '
            "or otherwise in a vulnerable situation, else false\n"
            '- "urgent": true for fraud, scams, unauthorised transactions or threats, else false\n'
            '- "summary": one sentence')


# Version 1 is the prompt in production: the 2017 names, those of the routing
# table. Version 2 is the release behind incident I1: it adopts the names the
# regulator introduced in August 2023, which the routing table does not know.
PROMPTS = {"v1": (_prompt(PRODUCTS), PRODUCTS), "v2": (_prompt(PRODUCTS_2023), PRODUCTS_2023)}


def validate(record: dict, names: list[str]) -> dict:
    """Keep the LLM's answer only if it has every field, with the right types."""
    if record.get("product") not in names:
        raise ValueError(f"product not among the options: {record.get('product')!r}")
    if not isinstance(record.get("issue"), str) or not record["issue"].strip():
        raise ValueError("no issue")
    for flag in ("vulnerable", "urgent"):
        if not isinstance(record.get(flag), bool):
            raise ValueError(f"{flag} is not true or false")
    return record


def read_with_llm(client, narrative: str) -> dict | None:
    """Ask the LLM; retry when its answer fails validation."""
    system, names = PROMPTS["v2" if fault_on("triage-v2") else "v1"]   # which release is live
    for _ in range(1 + INVALID_RETRIES):
        # The complaint goes in as the user message, the instructions as the system prompt.
        try:
            # 1,024 tokens: room for a thinking model's reasoning before the
            # JSON. At 300, Claude Haiku 5.5 was cut off on 3 attempts in 4.
            text = llm.call(client, "triage", system, narrative, max_tokens=1024)
            record = validate(json.loads(text), names)
        except (json.JSONDecodeError, ValueError):
            # Cut off (llm.Truncated is a ValueError), not JSON, or JSON that
            # fails validation: try again, paying again.
            ATTEMPTS.labels("invalid").inc()
            continue
        ATTEMPTS.labels("valid").inc()
        return record
    return None


def route(complaint_id: str, received: float, team: str, outcome: str, **fields) -> None:
    """Put the complaint on a team's queue, and record where it went."""
    REDIS.hset(f"complaint:{complaint_id}", mapping={
        "status": "routed", "team": team, "routed_at": time.time(), **fields})
    REDIS.rpush(f"queue:team:{team}", complaint_id)
    OUTCOMES.labels(outcome).inc()
    ROUTED_TO.labels(team).inc()
    TRIAGE_TIME.observe(time.time() - received)


def process(client, complaint_id: str) -> None:
    record = REDIS.hgetall(f"complaint:{complaint_id}")
    if not record:
        return   # expired before anyone got to it
    received = float(record["received_at"])   # set by the gateway, Unix time
    narrative = record["narrative"]
    if len(narrative) > MAX_CHARS:
        TRUNCATED.inc()
        narrative = narrative[:MAX_CHARS]

    level = degradation_level()
    # Level 4: no automated triage, people at the dispatch desk read everything.
    if level >= 4:
        return route(complaint_id, received, "dispatch", "dispatch")
    # Level 3: keyword rules instead of the LLM.
    if level == 3:
        product = rules.classify(narrative)
        return route(complaint_id, received, ROUTING[product], "rules", product=product)

    try:
        answer = read_with_llm(client, narrative)
    except Exception:  # noqa: BLE001 - the LLM could not be reached
        # A gap kept on purpose (lesson 1): nothing routes the complaint, so it
        # waits, unanswered, until its deadline passes. What should happen?
        REDIS.hset(f"complaint:{complaint_id}", "status", "triage_failed")
        OUTCOMES.labels("failed").inc()
        return
    if answer is None:
        # Every attempt failed validation: a person will triage it.
        return route(complaint_id, received, "dispatch", "dispatch")

    product = answer["product"]
    team = ROUTING.get(product, "unassigned")   # a name the table does not know
    route(complaint_id, received, team, "unassigned" if team == "unassigned" else "routed",
          product=product, issue=answer["issue"],
          vulnerable=int(answer["vulnerable"]), urgent=int(answer["urgent"]))


def worker(client) -> None:
    while True:
        # Wait up to 5 s for work; several workers share one queue safely.
        item = REDIS.blpop("queue:triage", timeout=5)
        if item:
            process(client, item[1])


def main() -> None:
    start_http_server(9100)                 # Prometheus scrapes this worker on :9100
    client = llm.client(timeout=30)         # one client, shared by every worker thread
    for _ in range(WORKERS):
        threading.Thread(target=worker, args=(client,), daemon=True).start()
    # The main thread only reports the queue and the level.
    while True:
        QUEUE_DEPTH.set(REDIS.llen("queue:triage"))
        LEVEL.set(degradation_level())
        time.sleep(2)


if __name__ == "__main__":
    main()
