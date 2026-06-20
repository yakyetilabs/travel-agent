# Travel Pre-Qualification Multi-Agent System

## What this project is

A portfolio multi-agent system built on Google's Agent Development Kit (ADK 2.0).
Four specialist agents live in this repo — `intake`, `fare_prep`, `policy`, and
`finalizer` — composed by an `orchestrator` SequentialAgent. The fare engine
itself lives in a separate Go repo and is called over A2A as a remote Cloud Run
service (`fare_engine`, a `RemoteA2aAgent`).

The orchestrator runs a SequentialAgent pipeline:
intake → fare_prep → fare_engine (RemoteA2aAgent) → policy → finalizer.

Why this order: `fare_prep` deterministically translates the human-shaped intake
(airports, dates, cabin) into the engine's pricing contract (distance,
advance-purchase days, route_type, season_code, booking_class) — see
`tools/fare_request.py`. The engine prices it, and `policy` runs *after* so its
budget check can act on the real quoted `total_fare` rather than guessing before a
fare exists. `finalizer` assembles the structured `TravelQualificationOutput`.

## Stack

- Python 3.12 (managed by pyenv, env by uv)
- google-adk[a2a] 2.0 Beta — agent framework
- Gemini 2.5 Pro for reasoning agents, Gemini 2.5 Flash for intake parsing
- Cloud Run for deployment
- `google-adk[mcp]` extra is installed for future MCP use (e.g. a sqlite audit
  log in the finalizer). NOT wired up yet — there is currently no MCP server
  config and no audit log. Treat this as a planned extension, not existing
  behavior.

## Code conventions

- Each ADK agent lives in `agents/<name>/agent.py` with a top-level `root_agent`
  variable so `adk run agents/<name>` works.
- Tools are pure Python functions in `tools/`. Tools must be deterministic — no LLM
  calls inside tools. The LLM calls tools; tools do not call LLMs.
- Use Pydantic models for all structured agent outputs. Define them in
  `agents/<name>/schemas.py`.
- Type hints are required on all public functions.
- Tests use pytest, live in `tests/`, mirror the source layout.

## What ADK is (so you don't hallucinate APIs)

- Agents inherit from `google.adk.agents.LlmAgent`.
- Workflows use `SequentialAgent`, `ParallelAgent`, `LoopAgent` from
  `google.adk.agents`.
- Tools are registered via the `tools=[...]` constructor argument.
- A2A client: `from google.adk.agents.remote_a2a_agent import RemoteA2aAgent`
  (lowercase `a` in `A2a`). See `agents/orchestrator/agent.py` for the
  ID-token auth pattern required by Cloud Run IAM.
- Local dev UI: `adk web` from repo root.
- If you are unsure of an ADK API, check https://google.github.io/adk-docs/
  before writing code. Do not guess.

## Deterministic tool rule

The policy agent must NEVER make budget, travel class, or advance purchase decisions
in the LLM. All policy checks are Python functions. The LLM gathers inputs, calls the
tools, and explains the result. Math or policy logic in a prompt is a bug.

## Data hygiene

All sample traveler data must be obviously fake (e.g., "Test Traveler",
"123 Fake St", destination "Nowhere City"). Never use real employee names,
real addresses, or anything that resembles real PII. This is non-negotiable.

## Common commands

- Run an agent locally: `adk run agents/<name>`
- Launch dev UI: `adk web`
- fare_engine is a separate Go service; see its repo. Point `FARE_ENGINE_URL`
  in `.env` at the deployed Cloud Run URL (or `http://localhost:8081` for local dev).
- Run evals: `adk eval agents/<name> eval/<name>.evalset.json`
- Deploy: `gcloud run deploy travel-prequal --source .`

## Don't do this

- Don't add libraries without asking. We're keeping deps minimal.
- Don't restructure the agents/ layout — `adk run` depends on it.
- Don't commit `.env` or anything with real PII.
- Don't write integration tests that hit live APIs in CI without an opt-in flag.

## References for further detail

- ADK Python docs: https://google.github.io/adk-docs/
- A2A protocol: see `.claude/skills/adk-agent-pattern/SKILL.md` for our usage
- Eval format: `.claude/skills/run-adk-local/SKILL.md`

## Eval-driven iteration

Any non-trivial change to an agent's prompt, tools, or model must be
preceded by `adk eval agents/<name> eval/<name>.evalset.json`. Capture
the baseline, make the change, re-run. If pass rate drops, decide:
agent regression (fix it) or reference drift (update the evalset).
Never both in the same commit.

## Pydantic + Gemini structured output gotcha

Gemini's response_schema validator does NOT support `exclusiveMinimum`
or `exclusiveMaximum`. In Pydantic Field constraints:

- Use `ge=` not `gt=`
- Use `le=` not `lt=`
  Otherwise you'll get `pydantic_core._pydantic_core.ValidationError:
... exclusiveMinimum` at agent invocation time, not at agent construction
  time — which makes it harder to spot. The constraint is the same
  semantically; only the JSON Schema output differs.

## ADK output_schema disables transfer_to_agent and tools

Setting `output_schema=...` on an LlmAgent disables both `transfer_to_agent`
(sub-agent delegation) and tool calling. The agent will produce structured
output but cannot delegate or invoke tools.

The pattern for orchestrators that need both delegation AND structured
output: use `SequentialAgent` with sub-agents that write to session state via
`output_key=...`, plus a final "finalizer" `LlmAgent` that reads state via
`{key}` template substitution and produces the structured output through
`output_schema`.

For agents that need both tools AND structured output (like policy),
omit `output_schema` and have the agent emit JSON-as-text in the prompt;
`output_key` will then store that text in state for downstream consumption.

The trace symptom of this gotcha: the orchestrator stops after the first
sub-agent and never delegates further.
