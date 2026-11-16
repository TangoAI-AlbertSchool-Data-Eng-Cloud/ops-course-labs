"""The nightly regulatory report: what was answered, by team, and how fast.

Regulators expect complaint statistics; the team needs them every morning.
Nobody waits on the job, so its health is not latency: it is whether it
finished, and finished before people needed its result.

In the lab, a "night" lasts REPORT_INTERVAL seconds (300 by default) and the
report is due REPORT_DEADLINE seconds after the start (120 by default).
"""

import json
import os
import time
from collections import Counter as Tally

from prometheus_client import Counter, Gauge, start_http_server

from lab.common import REDIS, fault_on

INTERVAL = int(os.environ.get("REPORT_INTERVAL", "300"))
DEADLINE = int(os.environ.get("REPORT_DEADLINE", "120"))

RUNS = Counter("batch_runs_total", "Report runs, by result", ["outcome"])
LAST_SUCCESS = Gauge("batch_last_success_timestamp_seconds", "When the last run succeeded (Unix time)")
DURATION = Gauge("batch_duration_seconds", "How long the last run took")
ROWS = Counter("report_complaints_total", "Answered complaints counted in reports")


def run_once(since: float) -> None:
    start = time.time()
    ids = REDIS.zrangebyscore("answered", since, start)   # answered since the last run
    tally = Tally()   # (team, answered in time?) -> how many
    for i, complaint_id in enumerate(ids):
        if fault_on("report-fails") and i >= len(ids) // 2:
            raise RuntimeError("crashed half-way through the report")
        # Both fields were written by the world when the complaint was answered.
        team, within = REDIS.hmget(f"complaint:{complaint_id}", "team", "within_deadline")
        tally[(team or "unknown", within or "unknown")] += 1
    # The report itself: one JSON document per night, kept for a day.
    report = {"from": since, "to": start,
              "rows": [{"team": t, "within_deadline": w, "complaints": n} for (t, w), n in sorted(tally.items())]}
    REDIS.set(f"report:{int(start)}", json.dumps(report), ex=24 * 3600)
    ROWS.inc(len(ids))
    took = time.time() - start   # seconds, compared with the deadline below
    DURATION.set(took)
    # Finishing late is a failure of its own: the team read yesterday's figures.
    if took > DEADLINE:
        RUNS.labels("late").inc()
    else:
        RUNS.labels("success").inc()
        LAST_SUCCESS.set(time.time())


def main() -> None:
    start_http_server(9100)
    # Until the first success, "last success" is the job's start: a job that
    # never succeeds then shows its age growing, rather than no data at all.
    LAST_SUCCESS.set(time.time())
    since = time.time()
    while True:
        started = time.time()
        try:
            run_once(since)
            since = started
        except Exception:  # noqa: BLE001 - a crashed run is recorded and the next one tried
            RUNS.labels("failed").inc()
        # Start the next run one interval after this one started.
        time.sleep(max(0, INTERVAL - (time.time() - started)))


if __name__ == "__main__":
    main()
