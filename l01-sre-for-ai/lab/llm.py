"""One way to call the LLM, shared by triage and the drafter.

`LLM_MODE=stub` (the default) points the real `anthropic` SDK at the lab's
stand-in server. `LLM_MODE=real` lets the SDK find a real key in the
environment, from the `.env` file (see the README): every call is then billed.
"""

import os
import time

import anthropic
from prometheus_client import Counter, Histogram

MODE = os.environ.get("LLM_MODE", "stub")
MODEL = os.environ.get("LLM_MODEL", "claude-haiku-5-5")

# Prices in USD per million tokens, Claude Haiku 5.5, prompts up to 100,000
# tokens (Anthropic pricing page, read 2026-11-05). In stub mode the token
# counts are simulated, so the cost is too: it shows the shape of the bill.
PRICE_PER_MTOK = {"input": 0.10, "output": 0.50}

CALLS = Counter("llm_requests_total", "LLM calls made by our code",
                ["service", "model", "outcome"])
TOKENS = Counter("llm_tokens_total", "Tokens billed", ["service", "model", "direction"])
COST = Counter("llm_cost_usd_total", "What the calls cost, in USD", ["service", "model"])
# Why the model stopped writing: "end_turn" when it had finished,
# "max_tokens" when our cap cut it off. A model that thinks before it answers
# (Claude Haiku 5.5 does, by default) spends part of the cap on thinking.
STOPS = Counter("llm_stop_reasons_total", "Why the model stopped writing",
                ["service", "model", "reason"])
DURATION = Histogram("llm_request_duration_seconds", "Time for one LLM call, retries included",
                     ["service", "model"], buckets=(0.25, 0.5, 1, 2, 4, 8, 16, 32))


class Truncated(ValueError):
    """The answer was cut off by `max_tokens`: half a JSON object, half a letter."""


def client(timeout: float) -> anthropic.Anthropic:
    """The SDK client. It retries twice by itself on 429, 529 and timeouts:
    one call from our code can be three requests to the provider."""
    if MODE == "real":
        return anthropic.Anthropic(timeout=timeout, max_retries=2)
    return anthropic.Anthropic(base_url=os.environ.get("LLM_STUB_URL", "http://llm:8000"),
                               api_key="stub-not-a-key", timeout=timeout, max_retries=2)


def call(llm: anthropic.Anthropic, service: str, system: str, prompt: str, max_tokens: int) -> str:
    """Send one prompt, record what it cost, return the text."""
    start = time.perf_counter()
    try:
        message = llm.messages.create(model=MODEL, max_tokens=max_tokens, system=system,
                                      messages=[{"role": "user", "content": prompt}])
    except anthropic.APIStatusError as error:
        # 429 rate limit, 529 overloaded, 500...: counted by status code.
        CALLS.labels(service, MODEL, f"http_{error.status_code}").inc()
        raise
    except anthropic.APIConnectionError:
        # Timeouts and refused connections.
        CALLS.labels(service, MODEL, "connection_error").inc()
        raise
    finally:
        DURATION.labels(service, MODEL).observe(time.perf_counter() - start)

    CALLS.labels(service, MODEL, "ok").inc()
    used_in, used_out = message.usage.input_tokens, message.usage.output_tokens
    TOKENS.labels(service, MODEL, "input").inc(used_in)
    TOKENS.labels(service, MODEL, "output").inc(used_out)
    COST.labels(service, MODEL).inc(
        (used_in * PRICE_PER_MTOK["input"] + used_out * PRICE_PER_MTOK["output"]) / 1_000_000)
    # Counted: without it, a cut-off answer looks like a bad one.
    reason = message.stop_reason or "unknown"
    STOPS.labels(service, MODEL, reason).inc()
    if reason == "max_tokens":
        raise Truncated(f"cut off at max_tokens={max_tokens}")
    # Only the text blocks: a thinking model's reply starts with a thinking block.
    return "".join(block.text for block in message.content if block.type == "text")
