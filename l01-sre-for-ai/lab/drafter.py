"""The drafter: writes a first reply for the agent handling a complaint.

POST /drafts  {complaint_id} -> {draft, source}

An agent opens the complaint and asks for a draft: the agent waits on the
answer, so this is the platform's synchronous LLM call. The LLM writes it;
when the LLM fails, or the platform runs at degradation level 2 or more, a
fixed template is used instead: plainer, and the agent edits more of it.
"""

from fastapi import FastAPI, HTTPException
from prometheus_client import Counter
from pydantic import BaseModel

from lab import llm
from lab.common import REDIS, degradation_level, instrument

app = FastAPI(title="drafter")
instrument(app, "drafter")

# A short timeout: an agent is waiting. Past it, the template is used.
client = llm.client(timeout=5)

DRAFTS = Counter("drafts_total", "Reply drafts, by how they were written", ["source"])

SYSTEM = ("DRAFT. You write the first draft of a reply to a customer complaint, for an agent "
          "to check and edit. Be plain and courteous, name the customer's issue, promise "
          "nothing the agent has not confirmed, and quote no figures.")


class Request(BaseModel):
    complaint_id: str


def template(issue: str) -> str:
    """The fallback draft: fixed wording, the issue filled in."""
    return (f"Dear customer,\n\nWe have received your complaint about: {issue.lower()}. "
            "A member of our team is reviewing it and will reply in writing within the "
            "time the regulations allow.\n\nKind regards,\nCustomer care")


@app.post("/drafts")
def draft(body: Request):
    record = REDIS.hgetall(f"complaint:{body.complaint_id}")
    if not record:
        raise HTTPException(status_code=404, detail="unknown complaint")
    issue = record.get("issue") or "your complaint"
    # Assume the fallback until the LLM has actually answered.
    source, text = "template", None
    if degradation_level() < 2:
        prompt = f"ISSUE: {issue}\nCOMPLAINT:\n{record['narrative'][:4000]}"
        try:
            text = llm.call(client, "drafter", SYSTEM, prompt, max_tokens=400)
            source = "llm"
        except Exception:  # noqa: BLE001 - any LLM failure falls back to the template
            text = None
    if text is None:
        text = template(issue)   # level 2+, or the LLM failed
    DRAFTS.labels(source).inc()
    return {"draft": text, "source": source}
