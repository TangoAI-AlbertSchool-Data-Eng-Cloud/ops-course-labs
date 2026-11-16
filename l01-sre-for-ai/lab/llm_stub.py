"""A stand-in for the Anthropic Messages API. It is NOT a language model.

The triage service and the drafter call it with the real `anthropic` SDK,
exactly as they would call Claude; only the base URL differs. Behind the API
it follows rules instead of reading:

* a triage request: it LOOKS UP the complaint in the lab's dataset and returns
  the category the consumer chose when filing it, under the names the prompt
  asks for, wrong on purpose 1% of the time (a real model makes mistakes too);
  a text it does not know is classified by the keyword rules of lab/rules.py;
* a draft request: it fills a fixed reply with the complaint's issue.

Why a stand-in: it is free, it gives the same answer every time, and it fails
on command (lab/common.py, FAULTS), so an incident can be replayed. Its
latencies and token counts are SIMULATED, chosen to resemble a small model's.
A real model must read the text; the README says how to try one.
"""

import asyncio
import hashlib
import json
import math
import random
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from lab import rules
from lab.common import fault_on
from lab.data import PRODUCTS, PRODUCTS_2023, is_vulnerable, load_complaints

app = FastAPI(title="LLM stand-in (Anthropic Messages API shape)")
rng = random.Random()

MISTAKE_RATE = 0.01   # share of triage answers that name the wrong product

# The keyword rules answer in the 2017 names; under the 2023 names, the two
# that changed are renamed (a card goes to "Credit card", the commoner kind).
TO_2023 = {
    "Credit card or prepaid card": "Credit card",
    "Payday loan, title loan, or personal loan": "Payday loan, title loan, personal loan, or advance loan",
}


def _key(text: str) -> str:
    """A complaint is found by a hash of its text: the stand-in sees only text."""
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


# Every complaint of the dataset, by the hash of its narrative.
KNOWN = {_key(c["narrative"]): c for c in load_complaints()}


def _text(content) -> str:
    """The API accepts a string or a list of content blocks; join the text."""
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")


def _tokens(text: str) -> int:
    """A rough token count, about four characters per token in English."""
    return max(1, math.ceil(len(text) / 4))


def _error(status: int, kind: str, message: str) -> JSONResponse:
    """An error in the shape the Anthropic API uses, so the SDK reacts as it would."""
    return JSONResponse(status_code=status,
                        content={"type": "error", "error": {"type": kind, "message": message}})


def _triage(system: str, narrative: str) -> str:
    """The triage answer as JSON: product, issue, vulnerable, urgent, summary."""
    known = KNOWN.get(_key(narrative))
    # The prompt says which names to use; like a real model, the stand-in obeys.
    names_2023 = "advance loan" in system
    names = PRODUCTS_2023 if names_2023 else PRODUCTS
    if known is None:
        # Not a complaint of the dataset (or cut short): guess by keywords.
        product, issue, vulnerable = rules.classify(narrative), "Other", False
        if names_2023:
            product = TO_2023.get(product, product)
    else:
        product = known["product_2023"] if names_2023 else known["product"]
        issue, vulnerable = known["issue"], is_vulnerable(known)
    if rng.random() < MISTAKE_RATE:
        # A plausible mistake: another product from the prompt's own list.
        product = rng.choice([p for p in names if p != product])
    urgent = any(word in issue.lower() for word in ("fraud", "scam", "unauthorized", "threat"))
    return json.dumps({
        "product": product,
        "issue": issue,
        "vulnerable": vulnerable,
        "urgent": urgent,
        "summary": " ".join(narrative.split())[:200],
    })


def _draft(prompt: str) -> str:
    """A first reply for the agent to edit, naming the complaint's issue."""
    issue = next((line[7:] for line in prompt.splitlines() if line.startswith("ISSUE: ")), "your concern")
    return (f"Dear customer,\n\nThank you for contacting us about: {issue.lower()}. "
            "We are sorry for the trouble this has caused. We have reviewed your account and "
            "the points you raised, and a member of our team will confirm the outcome in writing "
            "within the time the regulations allow.\n\nKind regards,\nCustomer care")


@app.post("/v1/messages")
async def messages(request: Request):
    body = await request.json()
    system = _text(body.get("system", ""))
    prompt = _text(body["messages"][-1]["content"])
    is_triage = "TRIAGE" in system

    # Provider-side failures, as the real API reports them.
    if fault_on("llm-overloaded") and rng.random() < 0.30:
        await asyncio.sleep(0.2)
        return _error(529, "overloaded_error", "Overloaded")

    out = _triage(system, prompt) if is_triage else _draft(prompt)
    # I2: a truncated answer, as when a model stops mid-object.
    if is_triage and fault_on("llm-malformed") and rng.random() < 0.04:
        out = out[: len(out) // 2]

    # Simulated generation time: a fixed start-up plus a time per output
    # token, with a long tail, as real providers show.
    input_tokens, output_tokens = _tokens(system + prompt), _tokens(out)
    seconds = (0.25 + output_tokens * 0.004) * rng.lognormvariate(0, 0.35)
    if fault_on("llm-slow"):
        seconds *= 8
    await asyncio.sleep(seconds)

    return {
        "id": f"msg_stub_{uuid.uuid4().hex[:20]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "unknown"),
        "content": [{"type": "text", "text": out}],
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }
