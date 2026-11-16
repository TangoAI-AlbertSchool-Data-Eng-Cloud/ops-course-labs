"""What every service shares: the Redis connection, the fault switches, the
degradation level, and HTTP metrics with bounded labels.
"""

import os
import time

import redis
from fastapi import Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

# One Redis serves three purposes in the lab: the queues, the complaints'
# state, and the switches below. In production these would be
# three different systems; here one container keeps the laptop free.
REDIS = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), port=6379, decode_responses=True)

# Every incident the lab can reproduce. `python -m lab.faults on <name>`
# switches one on; every service reads the switches from Redis.
FAULTS = {
    "triage-v2": "I1: a new triage prompt uses the regulator's 2023 category names",
    "llm-malformed": "I2: the LLM returns broken JSON on 4% of triage attempts",
    "llm-overloaded": "the LLM provider answers 529 'overloaded' to 30% of calls",
    "llm-slow": "the LLM provider answers eight times slower",
    "dispatch-short": "the dispatch desk loses half its staff",
    "report-fails": "the nightly regulatory report crashes half-way",
}

# Reading Redis on every request would double the lab's traffic, so each
# process caches the switches for one second.
_cache = {"at": 0.0, "faults": set(), "level": 1}


def _refresh() -> None:
    if time.monotonic() - _cache["at"] > 1.0:
        _cache["faults"] = set(REDIS.smembers("faults"))
        _cache["level"] = int(REDIS.get("degradation_level") or 1)
        _cache["at"] = time.monotonic()


def fault_on(name: str) -> bool:
    """Is this incident switched on?"""
    _refresh()
    return name in _cache["faults"]


def degradation_level() -> int:
    """The level the platform runs at: 1 full service ... 4 people triage everything."""
    _refresh()
    return _cache["level"]


# HTTP metrics, the same for every service.
# The `route` label is the route TEMPLATE ("/complaints/{complaint_id}"), never
# the path itself: a label holding every complaint id would create one time
# series per complaint, and the metrics backend would fill up with them.
REQUESTS = Counter("http_requests_total", "HTTP requests served",
                   ["service", "route", "code"])
LATENCY = Histogram("http_request_duration_seconds", "Time to answer an HTTP request",
                    ["service", "route"],
                    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))


# Pages that are not the service's work: Prometheus reading /metrics and the
# health check reading /docs. Counting them would dilute every SLI with
# requests no user ever made.
NOT_TRAFFIC = {"/metrics", "/docs", "/openapi.json"}


def instrument(app, service: str) -> None:
    """Count and time every request of a FastAPI app, and serve /metrics."""

    @app.middleware("http")
    async def measure(request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        # The matched route's template; unmatched paths share one label value.
        route = getattr(request.scope.get("route"), "path", "unmatched")
        if route not in NOT_TRAFFIC:
            REQUESTS.labels(service, route, str(response.status_code)).inc()
            LATENCY.labels(service, route).observe(time.perf_counter() - start)
        return response

    # Prometheus reads this page every few seconds.
    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
