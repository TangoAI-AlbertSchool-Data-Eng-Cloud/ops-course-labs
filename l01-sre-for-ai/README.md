# Lesson 1 lab: a complaint-handling platform, and its service level objectives

A consumer-finance company's complaint handling, small enough for a laptop:
the services that read and route complaints, the people who answer them, the
monitoring that watches it all, and switches that break it on purpose.

## What the platform does

Customers write complaints in their own words: a card charged twice, a debt
collector calling at night, a transfer that never arrived. Every complaint
must be answered within **15 business days** (the EU's second Payment
Services Directive, PSD2, Article 101). Nobody can route thousands of them a
day by hand, so a large language model (LLM) reads each one first:

```
customer -> gateway -> queue -> triage (LLM) -> a team's queue -> agent (+ draft from the LLM) -> answer
                                     |
                                     +-- product not in the routing table -> "unassigned" -> dispatch desk
```

| Step | Service | What it does | Kind of workload |
|---|---|---|---|
| 1. Receive | `gateway` | records the complaint, starts its 15-day clock, answers at once with an id | synchronous API (application programming interface) |
| 2. Triage | `triage` | the LLM reads the complaint: which product, which issue, is the customer vulnerable, is it urgent; the complaint goes to that product's team | asynchronous worker |
| 3. Draft | `drafter` | when an agent opens a complaint, the LLM drafts a first reply for them to edit | synchronous API |
| 4. Report | `report` | every "night" (five minutes here), counts what was answered, by team, and how fast | batch job |

Around them, `world` simulates everything outside the platform: customers
writing in, six teams of agents who **send back** any complaint that is not
theirs, a **dispatch desk** of people who re-route by hand what was sent
back or left unassigned, and the regulator's clock.

**What is real and what is not.**

- **The complaints are real**: 8,000 written by consumers to the US Consumer
  Financial Protection Bureau, about six products (`data/README.md`).
- **The LLM is not:** `llm` is a small stand-in that answers in the Anthropic
  API's format. For triage it **looks the complaint up** in the dataset and
  returns the category the consumer chose, wrong on purpose 1% of the time; a
  real model would have to read the text. Its response times, token counts and
  costs are simulated. It fails when you tell it to.
- **The agents are simulated**: how long they take follows the dataset (the
  complaints the real company answered late are answered late here too).
- **Time is compressed**: one business day is one minute, so the 15-day
  deadline is 15 minutes.

Also running: `redis` (the queues and the switches), `prometheus` (collects
every service's counters every 5 seconds and evaluates the alerts), `grafana`
(the dashboards).

## Start it

You need Docker Desktop running. In a terminal, in this folder:

```bash
docker compose up -d --build
```

The first build takes a few minutes. Then open:

- **Grafana**, the dashboard of service level objectives (SLOs): http://localhost:3000 (Dashboards, Lab, *Complaint platform: SLOs*)
- **Prometheus**, its alerts: http://localhost:9090/alerts
- **The gateway's API**: http://localhost:8400/docs

A port already taken on your machine? Create a file named `.env` in this
folder holding, for example, `GRAFANA_PORT=3300`, then run the command again.
The variables are `GATEWAY_PORT`, `PROMETHEUS_PORT` and `GRAFANA_PORT`.

Stop it with `docker compose down`; add `-v` to forget the metrics as well.

## Break it

Every incident is a switch. In a terminal, in this folder:

```bash
docker compose exec gateway python -m lab.faults list
docker compose exec gateway python -m lab.faults on triage-v2
docker compose exec gateway python -m lab.faults off all
```

The platform can also be degraded by level, which is what a runbook does
when a component fails:

```bash
docker compose exec gateway python -m lab.faults level 3
```

| Level | The platform runs with |
|---|---|
| 1 | full service |
| 2 | reply drafts from a fixed template, not the LLM |
| 3 | triage by keyword rules, not the LLM: right for 62.7% of the 8,000 complaints (`lab/rules.py`) |
| 4 | no automated triage: every complaint goes to the dispatch desk |

## Change the SLOs

The service level indicators (SLIs, what is measured) and their objectives
(SLOs, the targets) are in `slo/slos.py`. The Prometheus rules and the
dashboard are generated from it:

```bash
python slo/build.py
docker compose restart prometheus grafana
```

No Python on your machine? Docker can run it:

```bash
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python slo/build.py
```

## A real model instead of the stand-in (optional, billed)

Triage and the drafter use the real `anthropic` SDK (software development
kit). To send them to Claude, create a `.env` file in this folder with:

```
LLM_MODE=real
ANTHROPIC_API_KEY=sk-ant-...
```

then `docker compose up -d`, and lower the traffic first: at the default two
complaints per second the lab makes about 4 LLM calls a second. Set
`RATE: "0.1"` under `world` in `compose.yaml`. **Never commit `.env`.**

## Where the data comes from

`data/README.md`: the CFPB's Consumer Complaint Database (public domain).
