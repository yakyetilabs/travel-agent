# Travel Pre-Trip Approval - Multi-Agent Orchestrator

[![CI/CD](https://github.com/yakyetilabs/travel-agent/actions/workflows/deploy.yml/badge.svg)](https://github.com/yakyetilabs/travel-agent/actions/workflows/deploy.yml) ![Python](https://img.shields.io/badge/-Python-3776AB?logo=python&logoColor=white) ![Go engine](https://img.shields.io/badge/Engine-Go-00ADD8?logo=go&logoColor=white) ![Google ADK](https://img.shields.io/badge/Google%20ADK-4285F4?logo=google&logoColor=white) ![A2A Protocol](https://img.shields.io/badge/A2A-Protocol-6E44FF) ![Cloud Run](https://img.shields.io/badge/Cloud%20Run-4285F4?logo=googlecloud&logoColor=white) [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A two-service agentic system that turns a natural-language travel request into a policy-checked, auditable authorization decision.
The LLM is confined to the language boundary - understanding the request and explaining the outcome - while every dollar figure and every approve/deny verdict is produced by deterministic, unit-tested code.
That split is the whole point: it is the trust pattern for putting LLMs anywhere near money or compliance.

The **orchestrator** (this repo, Python) collects the trip, translates it into the pricing engine's contract, applies corporate policy against the real fare quote, and assembles the final decision.
The **pricing engine** is a standalone Go microservice, called over the Agent-to-Agent (A2A) protocol as a remote Cloud Run service.

> **📦 Two-repo system - clone both:**
>
> - 🧭 **This repo (start here):** [travel-agent](https://github.com/yakyetilabs/travel-agent)
> - ⚙️ **Pricing engine:** [travel-fare-engine](https://github.com/yakyetilabs/travel-fare-engine)

## Live demo

The deployed service runs privately on Cloud Run behind Identity-Aware Proxy (IAP); there is no public, unauthenticated endpoint.
A live, identity-gated demo is available to reviewers on request: email the Google account you will sign in with to **samirlama.dev@proton.me** and access is provisioned per identity.
That request flow is not demo scaffolding - it is the same access model the system runs in production: Google sign-in is the identity proof, checked against an IAM allowlist before any request reaches the container.

## Demo

A natural-language trip request runs through five specialist agents - intake, fare_prep, the remote fare engine, policy, and the finalizer - and comes out as a structured, policy-checked authorization built on the real fare quote. The final record is assembled in code, not transcribed by an LLM.

![The orchestrator running end to end: the agent graph fires intake, fare_prep, fare_engine, policy, and the finalizer, producing an approved decision with a populated fare quote (journey total $288.90)](docs/demo-pipeline.gif)

Under the hood, the execution trace. The remote fare engine is the dominant span, while policy's deterministic checks (`check_budget`, `check_advance_purchase`, and the rest) resolve in microseconds - the "deterministic core, LLM shell" design, visible in the timings.

![Execution trace waterfall: total invocation latency 18.86s, the remote fare_engine A2A call as the slowest span, and policy's tool-based checks completing in microseconds](docs/demo-trace.gif)

## Architecture

The orchestrator is an **ADK Workflow graph** (`agents/orchestrator/agent.py`): a linear chain from the graph's `START` node through five stages. Each stage is an ordinary ADK node - the migration to a graph did not change what the stages do, only how they are wired.

```
orchestrator_agent  -  ADK Workflow graph (START -> intake -> ... -> finalizer)

  intake        LlmAgent        collect trip + traveler fields          [output_schema]
  fare_prep     LlmAgent        marshal the trip into the engine request [deterministic tool]
  fare_engine   RemoteA2aAgent  remote Go service; prices the journey    [A2A]
  policy        LlmAgent        corporate policy against the real quote  [deterministic tools]
  finalizer     Workflow        nested graph: prose by LLM, structure by code
                  summary_writer       LlmAgent   the 1-3 sentence human summary   [output_key]
                  finalizer_assembler  BaseAgent  assembles the record in pure Python, no LLM
```

- **intake** talks to the user and gathers the required traveler and trip fields, including the trip type (one-way or round trip).
  When fields are missing it lists them in `missing_fields` and gates the result with `ready_for_policy=false`; downstream stages then degrade explicitly (fare_prep reports the gap, policy returns `needs_review`, the finalizer marks the outcome `incomplete`) rather than guessing.
  Passenger rules mirror the engine's booking constraints - at most 9 seated passengers (adults + children) and one lap infant per adult - enforced in the intake schema and again by the translator and the engine.
- **fare_prep** turns the human-shaped trip (airport codes, trip type, dates, cabin class) into the engine's exact `FareQuoteRequest`: a journey of directional **fare components**, one per leg.
  The LLM only calls the `build_fare_request` tool; the tool does all the derivation - distance, advance-purchase days, season code, and booking class per leg, so a December outbound and a January return price in different seasons and discount tiers.
- **fare_engine** is a remote A2A service written in Go. It prices each fare component independently and sums base fares and taxes deterministically into a journey-level `FareQuote`. The engine has its own inbound LLM guard that refuses to price incomplete requests, though under normal operation fare_prep guarantees completeness.
- **policy** evaluates the real `FareQuote` against budget, travel class, advance purchase, and trip-duration rules.
  Every check is a deterministic tool returning a three-way verdict: `pass`, `needs_approval` (a business cabin escalates to a manager), or `fail` (a first cabin, or any hard-rule breach, denies the trip).
  The $2000 cap is a trip budget cap: it applies to the quoted journey total - both legs of a round trip, all passengers on the booking, guests included.
  If the engine returns no quote (outage, timeout, or refusal to price), `check_budget` runs without a fare and returns `needs_approval`, so the trip escalates to a manager instead of being approved with its budget unverified.
  All thresholds are module constants in [tools/policy.py](tools/policy.py); the LLM neither applies policy itself nor passes thresholds to the tools.
- **finalizer** is a nested Workflow of two stages: `summary_writer` (LLM) writes the 1-3 sentence human summary - the only generative field in the output - and `finalizer_assembler` (a model-free custom agent) assembles `PreTripApprovalOutput` in pure Python from intake, policy, and fare results.
  The fare quote is copied verbatim from the current invocation's engine response, never retyped by a model, mirroring the engine's own deterministic quote passthrough.

## The A2A boundary

The standout of this system is the service boundary itself. The orchestrator and the fare engine are separate services - two languages, two repositories, independently deployable - that communicate **only** over the Agent-to-Agent (A2A) protocol.

- **Discovery, not hard-coding.** The orchestrator reads the engine's agent card at `/.well-known/agent-card.json` at runtime and builds the remote agent from it. Neither side imports the other's schema.
- **Input:** `FareQuoteRequest` - validated, derived values only. A journey of one (one-way) or two (round-trip) directional fare components.
- **Output:** `FareQuote` - journey totals (base fare, taxes, total), per-component fare basis codes and fare rules, and a quote ID.
- **Drift caught mechanically.** The shared enum vocabularies (cabin class, booking class, route type, season, passenger type, journey type, direction) are duplicated intentionally in both repos, and a CI tripwire ([tests/test_contract.py](tests/test_contract.py) here, `TestTripwire_*` in the engine) fails the build the moment they diverge. Duplication is the deliberate price of independent deployability.
- **Privacy by construction.** The engine never receives names, emails, employee IDs, departments, or even airport codes - only derived numerics such as distance in miles and advance-purchase days. Data minimization is enforced by the shape of the contract itself, which is what makes the engine's logs safe to retain and audit.

This is the answer to a real question: how do two teams evolve AI services independently, in different languages, without the integration rotting?

## What this project demonstrates

The travel domain is the vehicle, not the point. This system exists to demonstrate transferable principles for building trustworthy, enterprise-grade agentic systems. Swap travel for mortgages or insurance and every one still applies.

1. **Deterministic core, LLM shell.**
   LLMs never compute a number or make a policy decision. They translate human input into structured requests, decide when to call tools, and explain results.
   Every dollar figure and every approve/deny verdict comes from a pure, unit-tested function ([tools/fare_request.py](tools/fare_request.py), [tools/policy.py](tools/policy.py), and the engine's `Calculate`), and the final record is assembled in code ([agents/finalizer/assembler.py](agents/finalizer/assembler.py)). This is the trust argument for using LLMs near money or compliance.

2. **Hard contracts between independently deployable services.**
   Two repos, two languages, no shared library, on purpose - a published, discoverable A2A contract with drift caught by tripwire tests on both sides (see [The A2A boundary](#the-a2a-boundary)).

3. **Sequence decisions after facts exist.**
   The pipeline order is itself a correctness device: policy runs after pricing, so the budget decision consumes the real quoted journey total, never an estimate. Generalized: arrange the workflow so every decision-maker acts on ground truth that already exists.

4. **Typed state as the inter-agent interface.**
   Agents hand each other validated Pydantic/JSON structures through session state, not free-form prose. The repo documents the ADK-specific craft this requires - the output_schema-versus-tools trade-off, `output_key` templating, the deterministic finalizer pattern, and the SequentialAgent-to-Workflow migration (decisions in [docs/DECISIONS.md](docs/DECISIONS.md), gotchas in [docs/LESSONS.md](docs/LESSONS.md)).

5. **Engineering rigor applied to agents.**
   Eval sets pin tool trajectories for intake, fare_prep, and policy with a baseline-before-change discipline; contract tripwires run in CI; and unit tests prove the translator can never emit a request the engine would reject, across every advance-purchase day of the year ([tests/test_fare_request.py](tests/test_fare_request.py)). That is "make invalid states unrepresentable" applied across a service boundary.

## Security and delivery

The engineering around the agents is production-grade, even where the product surface is intentionally scoped (see [Known gaps](#known-gaps)):

- **Private by default.** Both services run on Cloud Run with `--no-allow-unauthenticated`; the orchestrator authenticates to the engine with a short-lived Google identity token (`_GCPIdTokenAuth`), and external reviewer access is gated by IAP per identity.
- **Least privilege.** Dedicated service accounts with minimal IAM (`roles/aiplatform.user` for Vertex AI, `roles/run.invoker` for the cross-service call).
- **Keyless CI/CD.** Workload Identity Federation - no service-account keys are stored or generated. Merge to main deploys to Cloud Run, with the deploy **gated on unit tests, the contract tripwire, and the ADK evals** (model-in-the-loop on Vertex AI).
- **Resilience.** The orchestrator's agents share one model definition with an explicit exponential-backoff-with-jitter retry budget; when it is exhausted the serving layer degrades to a structured, retryable `503 {"error": "model_busy"}` rather than a stack trace.
- **Stateless and horizontally scalable** - no session affinity.

## Run it yourself

```bash
uv sync
cp .env.example .env        # then fill in GEMINI_API_KEY and FARE_ENGINE_URL
```

Key env vars (see [`.env.example`](.env.example)):

- `FARE_ENGINE_URL` - the fare engine's base URL (`http://localhost:8081` locally).
- `GEMINI_API_KEY` - for local dev (or set `GOOGLE_GENAI_USE_VERTEXAI=TRUE` plus project/location).
- `GOOGLE_APPLICATION_CREDENTIALS` - only for local dev against a deployed, authenticated engine (mints the ID token). Unset in Cloud Run, where the metadata server is used automatically.

```bash
# Terminal 1: start the Go fare engine on :8081 (see its repo)
# Terminal 2: the orchestrator dev UI
adk web        # then open http://localhost:8000 and pick "orchestrator"
```

**Test and evaluate:**

```bash
pytest                                                                        # unit tests + contract tripwire (the enforced gate lives in tests/test_evals.py)
adk eval agents/intake    eval/intake.evalset.json    --config_file_path eval/test_config.json
adk eval agents/fare_prep eval/fare_prep.evalset.json --config_file_path eval/test_config.json
adk eval agents/policy    eval/policy.evalset.json    --config_file_path eval/test_config.json
```

The contract tripwire reads the engine's agent card and asserts the orchestrator's expected enums match; any drift breaks the build. The eval sets verify that, given a known conversation or session state, each agent calls the right tools with the right arguments - and baseline evals must pass before merging changes to agent prompts or tools.

**Deploy:**

```bash
gcloud run deploy travel-prequal --source .
```

The orchestrator's runtime service account must hold `roles/run.invoker` on the fare engine's Cloud Run service. See [docs/DEPLOY.md](docs/DEPLOY.md) and the engine's documentation for the full IAM setup.

## Known gaps

Deliberately scoped for a portfolio system; each is a known step toward a real deployment, not an oversight:

- **Incomplete requests run to completion instead of short-circuiting.** On a partial request the pipeline never fabricates an approval - it degrades explicitly to `incomplete` - but it still runs every stage rather than stopping to collect the missing fields first. The Workflow graph makes the natural fix in-architecture: a conditional edge that gates pricing on `ready_for_policy`, plus a human-in-the-loop clarification turn that asks for what is missing and resumes.
- **No persistence or audit log.** Quotes and decisions are returned to the caller but not stored. A production system would persist each decision - inputs, outputs, and timestamps - in tamper-evident storage for compliance and auditing.
- **Simplified fare engine.** The pricing engine uses a small set of hard-coded tables. A real deployment would integrate live ATPCO fares, corporate negotiated rates, or a GDS.
- **No automated rollback.** Cloud Run keeps the previous revision serving on failure, but the pipeline does not automatically revert or alert on a smoke-test failure.
- **Model rate-limit backoff covers the orchestrator, not yet the fare engine.** Under Vertex AI dynamic shared quota a call can be throttled with `429 RESOURCE_EXHAUSTED`. The orchestrator handles this with a shared retry budget and a structured `503` on exhaustion (see [docs/DECISIONS.md](docs/DECISIONS.md)); the engine's inbound LLM is the remaining exposed path.
- **Fare hold not honored.** Quote IDs are returned with an expiration, but there is no mechanism to guarantee the same fare if the user returns within the window.

## Further reading

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) - how the two services fit together.
- [docs/DECISIONS.md](docs/DECISIONS.md) - the design decisions and the alternatives rejected.
- [docs/DEPLOY.md](docs/DEPLOY.md) - stand up your own instance.
- [docs/LESSONS.md](docs/LESSONS.md) - the gotchas, and the concepts behind them.
