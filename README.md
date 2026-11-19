# Operating AI Systems at Scale: labs

The runnable labs for the Operating AI Systems at Scale course (Albert
School). The lessons themselves live on the course platform.

One folder per lesson, each runnable on its own with Docker Desktop and
nothing else: no cloud account, no paid tool, no API key.

| Folder | Lesson |
|---|---|
| `l01-sre-for-ai/` | Site Reliability Engineering (SRE) for AI: a complaint-handling platform run by a large language model (LLM), its four service level indicators (SLIs), its error budgets, and the incidents that test them |
| `l02-observability/` | Observability at scale: the same platform with traces and logs, produced with OpenTelemetry (OTel) and read in Grafana (Tempo, Loki) and Phoenix, sampling, and a mystery incident to find from the telemetry |
| `l03-governance/` | Governance and the EU AI Act: a credit-scoring platform, a high-risk AI system. **Published with lesson 3** |

Each folder is a snapshot of the platform as that lesson leaves it, so a
later lesson never depends on an earlier folder.
