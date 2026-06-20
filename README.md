# Travel Pre-Qualification Multi-Agent System

A portfolio multi-agent system built on Google's Agent Development Kit (ADK 2.0).
It pre-qualifies a corporate travel request: it collects the trip, prices it via a
separate fare-engine microservice, applies corporate policy, and returns a single
structured decision.

This is the **orchestrator** half of a two-repo system (and the best place to
start). The pricing half is a standalone Go A2A microservice
(`travel-fare-engine`) called over A2A as a remote Cloud Run service.

> **📦 Two-repo system — clone both:**
> - 🧭 **This repo (start here):** [yakyetilabs/travel-agent](https://github.com/yakyetilabs/travel-agent)
> - ⚙️ **Pricing engine:** [yakyetilabs/travel-fare-engine](https://github.com/yakyetilabs/travel-fare-engine)
>
> **New here?** Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the two
> fit together, [docs/DEPLOY.md](docs/DEPLOY.md) to stand up your own, and
> [docs/LESSONS.md](docs/LESSONS.md) for the gotchas (and the concepts behind
> them). Cloud gaps/status: [docs/CLOUD-READINESS.md](docs/CLOUD-READINESS.md).

## Architecture

```
orchestrator (SequentialAgent)
  ├─ intake      LlmAgent  — collect traveler + trip (airports, dates, cabin)   [output_schema]
  ├─ fare_prep   LlmAgent  — derive the engine's pricing request (deterministic tool)
  ├─ fare_engine RemoteA2aAgent — remote Go service; computes the fare quote
  ├─ policy      LlmAgent  — corporate policy checks incl. budget vs the real fare (tools)
  └─ finalizer   LlmAgent  — assemble TravelQualificationOutput                  [output_schema]
```

**Why the order:** the fare engine speaks pricing terms (distance, advance-purchase
days, route type, season, booking class); the traveler speaks human terms (airports,
dates, cabin). `fare_prep` ([`tools/fare_request.py`](tools/fare_request.py))
deterministically translates between them — the engine's own docs assign this
translation to the orchestrator. `policy` runs *after* the engine so its budget
check sees the real quoted `total_fare` instead of guessing.

## The two repos

| Repo | Role | Contract |
| ---- | ---- | -------- |
| `travel-agent` (this) | Orchestration, intake, policy, request derivation | Sends A2A `message/send`; consumes `FareQuote` |
| `travel-fare-engine` (Go) | Deterministic pricing | Exposes `compute_fare`; publishes agent card |

The enum vocabularies (cabin/booking/route/season/passenger) are **duplicated on
purpose** in both repos so they stay independently deployable. A tripwire test on
each side fails if they drift — see [`tests/test_contract.py`](tests/test_contract.py)
here and `internal/domain/fare/schema_test.go` in the engine.

## Setup

```bash
uv sync
cp .env.example .env        # then fill in GEMINI_API_KEY and FARE_ENGINE_URL
```

Key env vars (see [`.env.example`](.env.example)):

- `FARE_ENGINE_URL` — the fare engine's base URL (`http://localhost:8081` locally).
- `GEMINI_API_KEY` — for local dev (or set `GOOGLE_GENAI_USE_VERTEXAI=TRUE` + project/location).
- `GOOGLE_APPLICATION_CREDENTIALS` — only for local dev against a deployed,
  authenticated engine (mints the ID token). Unset in Cloud Run; the metadata
  server is used automatically.

## Run locally

```bash
# Terminal 1 — start the Go fare engine on :8081 (see its repo)
# Terminal 2 — the orchestrator dev UI
adk web        # then open http://localhost:8000 and pick "orchestrator"
```

See [`.claude/skills/run-adk-local/SKILL.md`](.claude/skills/run-adk-local/SKILL.md)
for the multi-process A2A workflow.

## Test & eval

```bash
pytest                                       # unit + contract tripwire tests
adk eval agents/intake   eval/intake.evalset.json
adk eval agents/fare_prep eval/fare_prep.evalset.json
```

Per `CLAUDE.md`, any non-trivial change to an agent's prompt, tools, or model must
be preceded by a baseline eval run. See [`eval/README.md`](eval/README.md).

## Deploy

```bash
gcloud run deploy travel-prequal --source .
```

The orchestrator's service account needs `roles/run.invoker` on the fare engine's
Cloud Run service. See `CLAUDE.md` and the engine repo's DECISIONS.md §12 for the
IAM pattern.
