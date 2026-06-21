# Travel Pre-Qualification Multi-Agent Orchestrator

The **orchestrator** half of a two-service corporate travel pre-qualification system. It collects trip and traveler information, translates it into the pricing engine’s contract, applies corporate policy against the real fare quote, and assembles a final structured decision. The pricing half — a standalone Go A2A microservice — is in the [`travel-fare-engine](https://github.com/yakyetilabs/travel-fare-engine) repository and is called over A2A as a remote Cloud Run service.

> **📦 Two-repo system — clone both:**
>
> - 🧭 **This repo (start here):** [travel-agent](https://github.com/yakyetilabs/travel-agent)
> - ⚙️ **Pricing engine:** [travel-fare-engine](https://github.com/yakyetilabs/travel-fare-engine)

## Architecture

```
orchestrator (SequentialAgent)
  ├─ intake      LlmAgent — conversational trip collection [output_schema]
  ├─ fare_prep   LlmAgent — deterministic translation to engine [deterministic tool]
  ├─ fare_engine RemoteA2aAgent — remote Go service; computes the fare and returns FareQuote
  ├─ policy      LlmAgent — corporate policy checks [tools]
  └─ finalizer   LlmAgent — assemble TravelQualificationOutput [output_schema]
```

- **intake** talks to the user and gathers all required traveler + trip fields. It will pause and ask for missing information before letting the workflow continue.
- **fare_prep** deterministically transforms the human‑shaped trip (airport codes, dates, cabin class) into the engine’s exact `FareQuoteRequest` — distances, advance‑purchase days, route type, season code, booking class. The LLM only calls the tool; the tool does all the derivation.
- **fare_engine** is a remote A2A service written in Go. It receives a fully‑specified request and returns a `FareQuote`. The engine contains its own LLM guard that refuses to price incomplete requests, but under normal operation that path is never triggered because fare_prep guarantees completeness.
- **policy** evaluates the real `FareQuote` against budget, travel class, advance purchase, and trip‑duration rules. All checks are deterministic tools; the LLM does not apply policy itself.
- **finalizer** assembles the final `TravelQualificationOutput` from intake, policy, and fare results.

## The two‑service boundary

The orchestrator and the fare engine communicate **only** through the A2A protocol. The contract is:

- **Input:** `FareQuoteRequest` — validated, derived values only (no PII, no raw airport codes).
- **Output:** `FareQuote` — a structured JSON object with base fare, taxes, fare rules, and a quote ID.
- **Discovery:** The engine publishes its capabilities via an agent card at `/.well-known/agent-card.json`. The orchestrator reads this card to create the remote agent — no hard‑coded schemas.

All enumerations (cabin class, booking class, route type, season, passenger type) are **duplicated intentionally** between the two repositories. A CI tripwire test (`tests/test_contract.py`) fails the build if the orchestrator’s local enum lists ever drift from the engine’s published card. This duplication is the price of independent deployability: each service can evolve on its own cadence as long as the A2A contract holds.

**Privacy by construction:** The fare engine never receives names, email addresses, employee IDs, department information, or even airport codes. It operates solely on derived numeric fields (distance in miles, passenger counts, advance‑purchase days). This boundary enforces data minimisation and makes the engine’s logs safe to retain and audit.

## Deterministic core

The critical business logic that converts a trip into a fare engine request lives in [`tools/fare_request.py`](tools/fare_request.py). It is pure Python, fully testable, and contains no LLM calls. The same applies to the policy checks and the engine’s own `compute_fare` tool. The LLM agents serve as **structured translation layers**: they decide _when_ to call their tools, but never perform the calculations themselves.

## Security posture

- **Cloud Run** with `--no-allow-unauthenticated` on both services.
- **Dedicated service accounts** with minimal IAM (`roles/aiplatform.user` for Vertex AI, `roles/run.invoker` for cross‑service calls).
- **Workload Identity Federation** for CI/CD — no service‑account keys stored or generated.
- The orchestrator authenticates to the engine with a short‑lived Google identity token (`_GCPIdTokenAuth`). When running locally against a local engine, authentication is skipped automatically.

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

## Test and evaluation

```bash
pytest                                      # unit tests + contract tripwire
adk eval agents/intake   eval/intake.evalset.json
adk eval agents/fare_prep eval/fare_prep.evalset.json
```

- **Contract tripwire:** tests/test_contract.py reads the engine’s agent card and asserts the orchestrator’s expected enums match. Any drift breaks the build immediately.

- **Evals:** Structured eval sets for intake and fare_prep verify that given a known conversation or session state, the agents call the right tools with the right arguments and produce the expected output. Baseline evals must pass before merging changes to agent prompts or tools.

## Deploy

```bash
gcloud run deploy travel-prequal --source .
```

The orchestrator’s runtime service account must hold `roles/run.invoker` on the fare engine’s Cloud Run service. See `CLOUDBUILD.md` and the engine’s documentation for the full IAM setup.

## What’s in place

- **Stateless orchestrator** — no session affinity, scales horizontally.
- **Pinned contract** — enum vocabularies duplicated and tripwire‑tested.
- **Deterministic translation** — build_fare_request computes distances, advance days, booking class, and season without an LLM.
- **A2A discovery** — the remote engine is configured via its agent card, not hard‑coded.
- **Eval harness** — intake and fare_prep evals with expected tool trajectories.
- **CI/CD** — PR checks include unit tests, tripwire, and evals (on agent‑relevant paths); merge deploys via Cloud Build.

## Known gaps

- **No persistence.** Quotes and decisions are returned to the caller but not stored. A production system would persist the full decision in a database for compliance and auditing.
- **No audit log.** Every qualification should be recorded with inputs, outputs, and decision timestamps in tamper‑evident storage.
- **Simplified fare engine.** The pricing engine uses a small set of hard‑coded tables. A real deployment would integrate with live ATPCO fares, corporate negotiated rates, or a GDS.
- **No automated rollback.** Cloud Run’s revision model keeps the previous version serving on failure, but the pipeline does not automatically revert or alert on smoke‑test failure.
- **Rate‑lock not honored.** Quote IDs are returned with an expiration, but there is no mechanism to guarantee the same fare if the user returns within the window.
- **Ingress for CI.** Cloud Run ingress is set to all (but still requires authentication) to allow GitHub‑hosted runners to reach the deployed service. A stricter posture would move smoke tests inside the project.

  **New here?**

> Read [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for how the two fit together.
> [DEPLOY.md](docs/DEPLOY.md) to stand up your own, and [LESSONS.md](docs/LESSONS.md) for the gotchas (and the concepts behind them).
