# Lesson 2 lab: the complaint platform, with traces and logs

Lesson 1's complaint platform, with what it lacked: **traces** and **logs**,
produced with OpenTelemetry (OTel), the open standard for them, and stored
by three tools you can read them in. The metrics, the service level
objectives (SLOs) and their dashboard are lesson 1's, unchanged.

## What the platform does

Customers write complaints in their own words: a card charged twice, a debt
collector calling at night, a transfer that never arrived. Every complaint
must be answered within **15 business days** (the EU's second Payment
Services Directive, PSD2, Article 101). A large language model (LLM) reads
each one first:

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
  returns the category the consumer chose, wrong on purpose 1% of the time.
  Its response times, token counts and costs are simulated. It fails when
  you tell it to. A real model can replace it (below).
- **The agents are simulated**: how long they take follows the dataset.
  An agent asks the drafter for a draft and **waits 10 seconds for it**,
  then writes the reply from scratch.
- **Time is compressed**: one business day is one minute, so the 15-day
  deadline is 15 minutes.

## What lesson 2 added

```
                     traces and logs (OTLP)                    every span
gateway, triage,  ------------------------->  collector  ----------------> tempo    (traces)
drafter, report,                                  |      -- LLM spans ---> phoenix  (LLM calls)
world                                             +------- every log -----> loki     (logs)

prometheus <- /metrics of every service, of the collector and of tempo (as in lesson 1, plus two)
grafana    <- prometheus, tempo, loki
```

| File | What it does |
|---|---|
| `lab/telemetry.py` | one `setup("<service>")` per service: the tracer, the logger and three instrumentations (FastAPI, httpx, the Anthropic SDK). Also `traceparent()` and `context_from()`, which carry the trace through the Redis queue, where no library can |
| `collector/config.yaml` | the OpenTelemetry collector: receives everything, sends every span to Tempo, **only the LLM spans** to Phoenix, every log record to Loki |
| `collector/tail-sampling.yaml` | the same, with tail sampling (below) |
| `tempo/tempo.yaml` | the trace store: keeps traces 24 hours |
| `grafana/provisioning/datasources/traces-and-logs.yaml` | Tempo and Loki in Grafana, linked both ways by the trace id |
| `compose.yaml` | lesson 1's nine containers, plus `collector`, `tempo`, `loki` and `phoenix` |

Loki has no file of its own: it runs on its image's default settings.

## Start it

You need Docker Desktop running, and **about 2 GB of memory** for the lab.
Stop lesson 1's lab first: the two use the same ports. In a terminal, in
this folder:

```bash
docker compose up -d --build
```

Then open:

- **Grafana**, http://localhost:3000: **Explore**, data source **Tempo**
  (traces) or **Loki** (logs); lesson 1's dashboard at
  http://localhost:3000/d/platform-slos
- **Phoenix**, the LLM calls, prompt and answer: http://localhost:6006
- **Prometheus**, its alerts: http://localhost:9090/alerts
- **The gateway's API**: http://localhost:8400/docs

A complaint's trace is complete once an agent has answered it, about three
minutes after it arrives.

A port already taken on your machine? Create a file named `.env` in this
folder holding, for example, `GRAFANA_PORT=3300`, then run the command again.
The variables are `GATEWAY_PORT`, `PROMETHEUS_PORT`, `GRAFANA_PORT` and
`PHOENIX_PORT`.

Stop it with `docker compose down`; add `-v` to forget the metrics and the
traces as well. `down` also empties every queue: the complaints in flight
are forgotten.

## Find things

| You want | Where |
|---|---|
| one complaint's trace | Grafana, Explore, Tempo: `{ span.complaint.id = "<id>" }` |
| slow complaints | Tempo: `{ span.triage.seconds_since_receipt > 30.0 }` (write `30.0`: the attribute is a decimal number, and `> 30` matches nothing) |
| slow LLM calls | Tempo: `{ name = "messages.create" && duration > 2s }` |
| what triage logged | Grafana, Explore, Loki: `{service_name="triage"} \|= "complaint routed"` |
| failures | Loki: `{service_name="triage"} \|= "triage failed"` |
| an LLM call's prompt and answer | Phoenix, project `default` |
| one service's log lines, raw | `docker compose logs -f triage` |

From a log line, **Open the trace**; from a span, **Logs for this span**.

## Break it

Every incident is a switch. In a terminal, in this folder:

```bash
docker compose exec gateway python -m lab.faults list
docker compose exec gateway python -m lab.faults on llm-slow
docker compose exec gateway python -m lab.faults off all
```

| Switch | Incident |
|---|---|
| `triage-v2` | I1: a new triage prompt uses the regulator's 2023 category names |
| `llm-malformed` | I2: the LLM returns broken JSON on 4% of triage attempts |
| `llm-overloaded` | the LLM provider answers 529 "overloaded" to 30% of calls |
| `llm-slow` | the LLM provider answers eight times slower |
| `dispatch-short` | the dispatch desk loses half its staff |
| `report-fails` | the nightly regulatory report crashes half-way |
| `trace-context-lost` | I3: the gateway stops passing the trace context to the triage queue. Each complaint's trace breaks in two |
| `label-per-complaint` | I4: a metric labelled with each complaint's id. Prometheus's series climb, and switching it off does not bring them down: restart triage |

**An incident you are not told about** (lesson 2, exercise 1): one of
three, drawn at random, starting at a random moment in the next four
minutes. `list` does not name it; find it from the telemetry.

```bash
docker compose exec gateway python -m lab.faults mystery
docker compose exec gateway python -m lab.faults reveal     # afterwards: what it was, and when it started
docker compose exec gateway python -m lab.faults off all    # also ends the mystery
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

## Settings in `.env`

Every setting below goes in a file named `.env` in this folder, one per
line. Then `docker compose up -d`: Docker recreates the containers the
change concerns. **Never commit `.env`.**

| Setting | What it does |
|---|---|
| `OTEL_TRACES_SAMPLER=parentbased_traceidratio` and `OTEL_TRACES_SAMPLER_ARG=0.1` | **head sampling:** keep one complaint's trace in ten, decided by the first service. Every metric is still complete |
| `COLLECTOR_CONFIG=tail-sampling.yaml` | **tail sampling:** the collector keeps every trace with an error or a complaint routed after 30 s, and one in ten of the rest (`collector/tail-sampling.yaml` says how) |
| `OPENINFERENCE_HIDE_INPUTS=true` and `OPENINFERENCE_HIDE_OUTPUTS=true` | **mask the LLM content:** the LLM spans keep the model, tokens and latency, and lose the prompt, the customer's letter and the answer |
| `DRAFT_TIMEOUT=5` | seconds the drafter gives the LLM for one attempt (5 by default). The SDK retries a timeout twice, so the drafter can take three times this, and more; the agent waits 10 s |
| `RATE=2` | complaints per second (2 by default) |

After changing `collector/config.yaml` or `collector/tail-sampling.yaml`
themselves, run `docker compose restart collector`: `up -d` does not see a
change inside a file.

## A real model instead of the stand-in

Triage and the drafter use the real `anthropic` SDK (software development
kit), so a real model needs no code change. **Never required**: the
stand-in is the lab. Lower the traffic first: at two complaints a second
the lab makes about four LLM calls a second.

**An open model, on your own graphics card, free: Ollama.** Install Ollama
(ollama.com), then in a terminal `ollama pull qwen2.5:3b` (1.9 GB). Ollama
answers the Anthropic API itself. Then this `.env`:

```
LLM_MODE=real
LLM_MODEL=qwen2.5:3b
ANTHROPIC_BASE_URL=http://host.docker.internal:11434
ANTHROPIC_API_KEY=ollama
RATE=0.1
```

`host.docker.internal` is your own machine, seen from a container, on
Docker Desktop (Windows, macOS). The key is not checked; the SDK only
needs one.

**Claude, billed:** `LLM_MODE=real`, your `ANTHROPIC_API_KEY=sk-ant-...`,
and `RATE=0.1`.

Then empty the queues and start again, so that no complaint sent at full
pace reaches the model:

```bash
docker compose down
docker compose up -d
```

Back to the stand-in: delete `.env` (or its lines), then the same two
commands.

**What changes** (lesson 2's lecture, section 8, measured on this lab): a
real model is slower and its times spread; a small open model fails
validation on about three triage answers in ten; **the drafter's 5-second
timeout is too short for it** (`DRAFT_TIMEOUT`, above), and the agent's
10 seconds bound what a longer timeout can save. Ollama on a laptop answers
one request at a time, so a slow draft delays triage too.

## Change the SLOs

The service level indicators (SLIs) and their objectives are in
`slo/slos.py`. The Prometheus rules and lesson 1's dashboard are generated
from it:

```bash
python slo/build.py
docker compose restart prometheus grafana
```

No Python on your machine? Docker can run it:

```bash
docker run --rm -v "${PWD}:/work" -w /work python:3.12-slim python slo/build.py
```

## Versions

Checked 2026-11-16: OpenTelemetry Python SDK 1.45.2, instrumentations
0.66b2, `openinference-instrumentation-anthropic` 3.3.5, collector-contrib
0.165.0, Tempo 2.10.10, Loki 3.7.10, Phoenix 21.1.0 (Elastic License 2.0:
free to self-host, source-available, not open source), Grafana 13.3.0,
Prometheus 3.15.1, Redis 8.8.4.

## Where the data comes from

`data/README.md`: the CFPB's Consumer Complaint Database (public domain).
