"""The gateway: where a complaint arrives and where its status is read.

It does no reading itself. It records the complaint, starts its clock (the
answer is due within 15 business days, the PSD2 rule, compressed to 15
minutes in the lab), answers at once with an id (202), and puts the work on
a queue for the triage service.
"""

import os
import time
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from lab.common import REDIS, instrument

# One "business day" lasts this many seconds in the lab (60: a minute).
DAY_SECONDS = float(os.environ.get("DAY_SECONDS", "60"))
DEADLINE_DAYS = 15        # PSD2 Article 101(2): a reply within 15 business days
KEEP_SECONDS = 2 * 3600   # complaint records expire after two hours

app = FastAPI(title="gateway")
instrument(app, "gateway")


class Complaint(BaseModel):
    customer_ref: str   # the customer's pseudonymous reference, never a name
    narrative: str      # what the customer wrote, in their own words


@app.post("/complaints", status_code=202)
def submit(complaint: Complaint):
    complaint_id = uuid.uuid4().hex
    now = time.time()
    due = now + DEADLINE_DAYS * DAY_SECONDS
    # The complaint's state lives in one Redis hash, read by every service.
    REDIS.hset(f"complaint:{complaint_id}", mapping={
        "customer_ref": complaint.customer_ref,
        "narrative": complaint.narrative,
        "status": "received",
        "received_at": now,
        "due_at": due,
    })
    REDIS.expire(f"complaint:{complaint_id}", KEEP_SECONDS)
    # The clock starts for every complaint: nobody knows its product yet, so
    # the company promises 15 days to all. The law binds only payment
    # products; the SLO counts those (slo/slos.py). Checked by the outside world.
    REDIS.zadd("deadlines", {complaint_id: due})
    # The triage service takes work from the other end of this list.
    REDIS.rpush("queue:triage", complaint_id)
    return {"id": complaint_id, "status": "received", "due_at": due}


@app.get("/complaints/{complaint_id}")
def status(complaint_id: str):
    record = REDIS.hgetall(f"complaint:{complaint_id}")
    if not record:
        raise HTTPException(status_code=404, detail="unknown complaint")
    # The narrative is not sent back: it holds personal details the caller
    # already has.
    return {key: value for key, value in record.items() if key != "narrative"}
