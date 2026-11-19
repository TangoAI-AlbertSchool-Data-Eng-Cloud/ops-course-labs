"""Lesson 2: traces and logs, sent with OpenTelemetry.

Every service calls `setup("<its name>")` once, at start. From then on:

* **Traces.** Each unit of work is a span; spans of one complaint share a
  trace id. They go to the OpenTelemetry collector (the `collector`
  container), which sends them on to Tempo, and the LLM spans to Phoenix.
* **Logs.** `log.info("routed", extra={...})` writes one structured record:
  printed as one line of JSON (`docker compose logs`), and sent to the
  collector, which sends it to Loki. Each record carries the trace id of the
  span it was written in, so a log line leads to its trace and back.
* **Metrics** stay as they were in lesson 1: Prometheus reads /metrics.

What is instrumented without writing a span by hand: every FastAPI request
(the server side), every httpx call (the client side, which also passes the
trace context on in a `traceparent` header), and every call through the
`anthropic` SDK (an LLM span with the model, the prompt and the tokens).

What no library can do for us: carry the context through a queue we built
ourselves on Redis. `traceparent()` and `context_from()` are the two halves
of that.
"""

import json
import logging
import os
import sys

from opentelemetry import propagate, trace
# The logs part of the Python SDK is still marked experimental (the leading
# underscore): its names may change between versions, which is why every
# version below is pinned in requirements.txt.
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# Where the collector listens for OTLP over HTTP (set in compose.yaml).
ENDPOINT = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318")

# One logger for the whole lab; each service's records carry its name.
log = logging.getLogger("lab")


class JsonLines(logging.Formatter):
    """One JSON object per line, with the trace id when there is one."""

    # Attributes every LogRecord has: everything else came from `extra=`.
    STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}

    def format(self, record: logging.LogRecord) -> str:
        line = {"level": record.levelname.lower(), "msg": record.getMessage()}
        line.update({k: v for k, v in vars(record).items() if k not in self.STANDARD})
        span = trace.get_current_span().get_span_context()
        if span.is_valid:
            line["trace_id"] = format(span.trace_id, "032x")   # 32 hex digits, as Tempo shows it
        return json.dumps(line)


def setup(service: str) -> trace.Tracer:
    """Start tracing and logging for one service; return its tracer."""
    # Who is talking: every span and log record carries these attributes.
    resource = Resource.create({
        "service.name": service,
        "service.namespace": "complaints",
        "deployment.environment.name": "lab",
    })

    # Spans are batched and sent in the background: a slow collector never
    # slows a complaint down, and a dead one only loses telemetry.
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{ENDPOINT}/v1/traces")))
    trace.set_tracer_provider(tracer_provider)

    # Log records go two ways: to the collector (then Loki), and to stdout.
    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{ENDPOINT}/v1/logs")))
    log.addHandler(LoggingHandler(level=logging.INFO, logger_provider=logger_provider))
    to_stdout = logging.StreamHandler(sys.stdout)
    to_stdout.setFormatter(JsonLines())
    log.addHandler(to_stdout)
    log.setLevel(logging.INFO)

    # Library instrumentation: imported here so a service that does not use
    # a library does not pay for it.
    from openinference.instrumentation.anthropic import AnthropicInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    HTTPXClientInstrumentor().instrument()          # outgoing HTTP, and the traceparent header
    AnthropicInstrumentor().instrument(tracer_provider=tracer_provider)   # LLM spans
    return trace.get_tracer(service)


def instrument_app(app) -> None:
    """Server spans for every request of a FastAPI app."""
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    FastAPIInstrumentor.instrument_app(
        app,
        # Prometheus and the health check read these pages every few seconds:
        # traced, they would bury the real work.
        excluded_urls="metrics,docs,openapi.json",
        # The ASGI layer adds two inner spans per request ("http send" and
        # "http receive") that say nothing the request span does not.
        exclude_spans=["receive", "send"],
    )


def traceparent() -> str:
    """The current span's context, as the W3C `traceparent` header value:
    version-traceid-spanid-flags, e.g. 00-4bf9...-00f0...-01."""
    carrier: dict[str, str] = {}
    propagate.inject(carrier)          # writes "traceparent" (and "tracestate" if any)
    return carrier.get("traceparent", "")


def context_from(value: str | None):
    """The context a `traceparent` value describes, or None without one:
    a span started in it becomes a child of the span that wrote it."""
    return propagate.extract({"traceparent": value}) if value else None
