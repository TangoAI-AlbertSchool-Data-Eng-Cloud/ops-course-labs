"""The platform's SLOs: the one file to edit.

Each entry is one service level indicator (SLI) and its objective (SLO).
`python slo/build.py` turns this list into the Prometheus rules (SLIs, burn
rates, alerts) and the Grafana dashboard. Never edit those two by hand: the
next build overwrites them.

An SLI here is written as the share of BAD events, `bad / total`, in PromQL,
over a window `{w}`; the build fills `{w}` in for every window it needs.
`(bad or vector(0))`: Prometheus has no series for an event that never
happened, so without it a platform with no errors at all would show no SLI.
Doubled braces `{{ }}` are PromQL's own braces, escaped for Python's format().
"""

TITLE = "Complaint platform: SLOs"

# The four families: one question each. Most teams stop at the first one.
FAMILIES = {
    "infrastructure": "Is the system up and fast?",
    "input-validity": "Is what goes into the model what the model expects?",
    "output-quality": "Is what comes out of the model still right?",
    "business-outcome": "Is the business getting what the model is for?",
}

# Helper series some SLIs are built on: (name, PromQL). Here, the 0/1 series
# behind the one window-based SLI: 1 when the dispatch desk's queue stayed
# under 50 complaints for the whole last five minutes.
INPUTS = [
    ("slo:desk_queue_short", "max_over_time(dispatch_queue_depth[5m]) < bool 50"),
]

SLOS = [
    {
        # Infrastructure, for an ASYNCHRONOUS workload: nobody waits on a
        # request; they wait for their complaint to reach someone.
        "name": "triaged-in-time",
        "family": "infrastructure",
        "workload": "async",
        "headline": True,
        "description": "Complaints routed to a queue within 60 s of arriving",
        "objective": 0.99,
        # Counted where a complaint STARTS (the gateway), not where it ends: a
        # complaint stuck before triage never ends, and would never count.
        # Note le="60.0", not "60": the Python client writes bucket bounds as floats.
        "error_ratio": (
            "clamp_min(1 - sum(rate(complaint_triage_seconds_bucket{{le=\"60.0\"}}[{w}]))"
            " / sum(rate(http_requests_total{{service=\"gateway\",route=\"/complaints\"}}[{w}])), 0)"
        ),
    },
    {
        # Input validity, per LLM ATTEMPT rather than per complaint: triage
        # retries, so a complaint rarely fails, but every bad attempt is paid
        # for and is a sign of trouble.
        "name": "valid-triage",
        "family": "input-validity",
        "workload": "async",
        "headline": True,
        "description": "LLM triage attempts that return a complete, valid answer",
        "objective": 0.99,
        "error_ratio": (
            "(sum(rate(triage_attempts_total{{result=\"invalid\"}}[{w}])) or vector(0))"
            " / sum(rate(triage_attempts_total[{w}]))"
        ),
    },
    {
        # Output quality, checked on every answer the moment it is given, with
        # no human verdict needed: does the product the LLM chose lead to one
        # of the six teams? The validator checks the answer against the
        # prompt's own list; the routing table belongs to another team, so the
        # two can disagree. In normal operation none is unassigned.
        "name": "reached-a-team",
        "family": "output-quality",
        "workload": "async",
        "headline": True,
        "description": "Triaged complaints whose product leads to a team (not left unassigned)",
        "objective": 0.999,
        "error_ratio": (
            "(sum(rate(triage_outcomes_total{{outcome=\"unassigned\"}}[{w}])) or vector(0))"
            " / sum(rate(triage_outcomes_total{{outcome=~\"routed|unassigned|rules\"}}[{w}]))"
        ),
    },
    {
        # Business outcome: the answer the customer and the regulator care
        # about. Counted when each deadline arrives: met if answered by then.
        "name": "answered-in-time",
        "family": "business-outcome",
        "workload": "async",
        "headline": True,
        "description": "Payment-product complaints answered within 15 business days (PSD2), counted at each deadline",
        # The law asks for every reply within 15 business days (35 in exceptional
        # cases, with a holding reply), for payment services only: cards,
        # accounts and transfers. Loans and debt collection keep the company's
        # own 15-day promise, counted under psd2="no" but not an SLO. These
        # products normally miss about 1% (0.98% measured on 2026-11-09), so a
        # normal month spends about a quarter of the budget. 96% says how often
        # the law's exception (a holding reply, up to 35 days) may be used;
        # whether to raise it to 98% is a question for your team.
        "objective": 0.96,
        # Tickets only, never a page. A business outcome lags its cause: a
        # deadline is missed 15 business days after the complaint arrives, by
        # when the cause has been paging for a while (in I1, 16 minutes), and
        # a page now would wake a second person about a cause already being
        # handled. The build gives it the three-day ticket and no page.
        "pages": False,
        "error_ratio": (
            "(sum(rate(complaint_deadlines_total{{result=\"breached\",psd2=\"yes\"}}[{w}])) or vector(0))"
            " / sum(rate(complaint_deadlines_total{{psd2=\"yes\"}}[{w}]))"
        ),
    },
    # Not headline SLIs, but each covers what the four above miss.
    {
        # Output quality again, but the verdict comes LATER, from people: the
        # team that opens the complaint sends it back if it is not theirs, half
        # a business day to two days after triage. The LLM stand-in is wrong 1%
        # of the time on purpose, so normal operation spends about two thirds
        # of the 2% budget (1.4% measured on 2026-11-07).
        "name": "routed-right",
        "family": "output-quality",
        "workload": "async",
        "headline": False,
        "description": "Routed complaints the team keeps (not sent back as someone else's)",
        "objective": 0.98,
        "error_ratio": (
            "(sum(rate(routing_sent_back_total[{w}])) or vector(0))"
            " / sum(rate(triage_outcomes_total{{outcome=~\"routed|rules\"}}[{w}]))"
        ),
    },
    {
        # The synchronous API itself. Server errors only: a 404 for an unknown
        # complaint is the caller's mistake, not an outage.
        "name": "gateway-available",
        "family": "infrastructure",
        "workload": "sync",
        "headline": False,
        "description": "Gateway requests answered without a server error (5xx)",
        "objective": 0.999,
        "error_ratio": (
            "(sum(rate(http_requests_total{{service=\"gateway\",code=~\"5..\"}}[{w}])) or vector(0))"
            " / sum(rate(http_requests_total{{service=\"gateway\"}}[{w}]))"
        ),
    },
    {
        # The SYNCHRONOUS LLM call: an agent waits for the draft.
        "name": "drafts-fast",
        "family": "infrastructure",
        "workload": "sync",
        "headline": False,
        "description": "Reply drafts returned within 5 s",
        "objective": 0.99,
        "error_ratio": (
            "clamp_min(1 - sum(rate(http_request_duration_seconds_bucket{{service=\"drafter\",route=\"/drafts\",le=\"5.0\"}}[{w}]))"
            " / sum(rate(http_request_duration_seconds_count{{service=\"drafter\",route=\"/drafts\"}}[{w}])), 0)"
        ),
    },
    {
        # The BATCH workload: a run is good if it finishes, and on time. With
        # one run per "night", few events: a lower objective, and slow alerts.
        "name": "report-on-time",
        "family": "infrastructure",
        "workload": "batch",
        "headline": False,
        "description": "Nightly report runs that finish, and finish before the deadline",
        "objective": 0.95,
        "error_ratio": (
            "(sum(rate(batch_runs_total{{outcome!=\"success\"}}[{w}])) or vector(0))"
            " / sum(rate(batch_runs_total[{w}]))"
        ),
    },
    {
        # A WINDOW-BASED SLI: each 15-second evaluation is one "event", good if
        # the people at the dispatch desk had a short queue all through the
        # last five minutes (slo:desk_queue_short, above).
        "name": "desk-queue-short",
        "family": "infrastructure",
        "workload": "people",
        "headline": False,
        "description": "Five-minute windows in which the dispatch desk's queue stays under 50",
        "objective": 0.99,
        "error_ratio": "1 - avg_over_time(slo:desk_queue_short[{w}])",
    },
]

# The four graphs under the SLOs, to see WHY one moved: (title, [(query,
# legend)], unit). Legends in {{ }} are Grafana's, filled with a label value.
SIGNALS = [
    ("Where triage sent complaints (per minute)",
     [("sum by (outcome) (rate(triage_outcomes_total[5m])) * 60", "{{outcome}}")], None),
    ("Sent back or unassigned (per minute)",
     [("sum(rate(routing_sent_back_total[5m])) * 60", "sent back by a team"),
      ("sum(rate(triage_outcomes_total{outcome=\"unassigned\"}[5m])) * 60", "unassigned")], None),
    ("Dispatch desk queue",
     [("max(dispatch_queue_depth)", "complaints waiting")], None),
    ("Legal deadlines reached, payment products (per minute)",
     [("sum by (result) (rate(complaint_deadlines_total{psd2=\"yes\"}[5m])) * 60", "{{result}}")], None),
]
