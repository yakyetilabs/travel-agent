# Evals

ADK evalsets for the orchestrator's agents. These are the **end-to-end / model-in-the-loop**
half of the system's evaluation story; the fare engine's deterministic pricing is
pinned separately by the Go eval harness in the `travel-fare-engine` repo.

## Run

The scoring dependencies are an optional extra; install once with
`uv sync --extra eval`.

```bash
TRAVEL_CLOCK_TODAY=2026-07-07 adk eval agents/intake    eval/intake.evalset.json    --config_file_path eval/test_config.json
TRAVEL_CLOCK_TODAY=2026-07-07 adk eval agents/fare_prep eval/fare_prep.evalset.json --config_file_path eval/test_config.json
TRAVEL_CLOCK_TODAY=2026-07-07 adk eval agents/policy    eval/policy.evalset.json    --config_file_path eval/test_config.json
```

`TRAVEL_CLOCK_TODAY` freezes the **domain clock** (`tools/clock.py`) to
2026-07-07, the date these references were authored.
Every date-derived reference value ("N days in advance", `advance_purchase_days`,
booking classes) equals `departure - 2026-07-07`, so the frozen clock makes the
evalsets reproducible on any calendar day; unset, they rot one token per day and
hard-break once the 2026-09 trip dates pass (docs/LESSONS.md lesson 16,
docs/DECISIONS.md §8).
The pytest gate below sets the variable itself (autouse fixture), so only manual
CLI runs need the prefix.

The `--config_file_path` flag is required: the `adk eval` CLI does **not**
auto-discover `test_config.json` from the evalset's folder (only the pytest
`AgentEvaluator` API does). Without it the CLI silently grades with the ADK
defaults (`response_match_score: 0.8`) instead of this suite's deliberate thresholds.

To iterate on a single case, append `:<eval_id>` to the evalset path:

```bash
TRAVEL_CLOCK_TODAY=2026-07-07 adk eval agents/policy eval/policy.evalset.json:no_fare_quote_escalates --config_file_path eval/test_config.json
```

The CLI is for local inspection only: it always exits 0, even when evals fail,
so it cannot gate anything.
The enforced path is [`tests/test_evals.py`](../tests/test_evals.py), which wraps
the same evalsets in `AgentEvaluator` (asserts on failures, auto-discovers the
config) and runs when `RUN_ADK_EVALS=1`:

```bash
RUN_ADK_EVALS=1 uv run pytest tests/test_evals.py -v
```

Thresholds live in [`test_config.json`](test_config.json):

- `tool_trajectory_avg_score` (1.0) — did the agent call the right tools with the
  right args, in order? Exact match.
- `response_match_score` (0.5) — ROUGE overlap of the final response against the
  reference. Lenient on purpose (see caveat below).

## What each set checks

### `intake.evalset.json` (4 cases)

Natural language → structured `IntakeOutput`.
Intake has no tools, so grading is purely response match.
`trip_type` must be inferred only when the traveler is explicit.

| Case | What passes it |
|---|---|
| `complete_single_adult_economy` | A one-message round-trip request lands every traveler and trip field in the right schema slot, with `ready_for_policy=true` and empty `missing_fields`. |
| `complete_one_way` | Explicit "one-way" phrasing yields `trip_type="one_way"` and `return_date=null`. |
| `missing_fields_gated` | Every absent field is listed in `missing_fields` and nulled in the output - including `trip_type`, which must not be guessed from "a trip for a conference" - with `ready_for_policy=false`. |
| `complete_family_premium_economy` | A multi-passenger booking preserves the exact passenger counts and types, and `premium_economy` survives verbatim instead of collapsing into `economy`. |

### `fare_prep.evalset.json` (4 cases)

Reads `{intake_output}` from `session_input.state`
and must call `build_fare_request` exactly once with the trip's
origin/destination/trip_type/dates/class/passengers (omitting `return_date`
for one-way trips). Graded primarily on the **tool trajectory**, which is
deterministic. The *derived* `fare_request` (per-leg fare components: distance,
booking class, season, advance-purchase days) is date-derived; under the
frozen domain clock (see Run above) it reproduces the reference exactly, and
`response_match` stays lenient as defense in depth.

| Case | What passes it |
|---|---|
| `domestic_round_trip_single_adult` | Exactly one tool call carrying all seven trip fields; the derived request splits the round trip into outbound and return fare components. |
| `one_way_single_adult` | The `return_date` argument is omitted entirely (not passed as null) and the derived request has a single outbound component. |
| `international_mixed_pax` | The mixed passenger list (2 adults + 1 child) passes through untouched and the derived request is `route_type="international"`. |
| `unknown_airport_error_passthrough` | The tool's `{"ok": false, "error": ...}` comes back verbatim - no invented fare, no "corrected" airport code, no retry. |

### `policy.evalset.json` (5 cases)

Pins the policy agent's **LLM-owned** behaviors only:
which tools it calls, argument transcription (`total_fare` from the FareQuote in
conversation history, dates and cabin from `{intake_output}` state), and faithful
explanation of the tools' three-way verdicts.
Threshold math and verdict boundaries are deterministic and owned by
`tests/test_policy.py`; they are deliberately not re-tested here.
The user message stands in for the pipeline's conversation history, carrying the
fare engine's FareQuote JSON or, in the degraded case, the A2A failure.

| Case | What passes it |
|---|---|
| `all_pass_round_trip_approved` | All four check tools fire with exact arguments; `total_fare=288.90` is transcribed from the quote's journey total; the decision is `approved`. |
| `business_cabin_needs_review` | A business cabin produces `needs_review` with `requires_manager_approval=true`, not a denial and not an approval. |
| `first_cabin_one_way_denied` | A first cabin produces `denied`, and `check_max_trip_duration` is skipped because the trip is one-way. |
| `no_fare_quote_escalates` | The A2A failure text still produces a `check_budget` call with **no arguments** and a `needs_review` decision - never an approval with the budget unverified (the 2026-06 outage regression). |
| `multi_passenger_journey_total_fidelity` | The budget argument is the quoted **journey total** ($1758.02 - both legs, all passengers), not a per-leg or per-passenger number. |

## Roadmap: planned cases

The 13 cases above are all active.
The cases below are documented before their JSON exists, to make the intent of the suite visible; each row names what blocks it, if anything.

| Agent | Case | What it would test | Why not active yet |
|---|---|---|---|
| intake | `multi_turn_clarification` | The 2-turn path: a gated first turn asks for the missing fields, the traveler supplies them, and the second turn produces a complete `IntakeOutput`. The most representative shape of real intake use. | Authoring effort only - multi-turn cases need a reference per invocation. |
| intake | `passenger_limit_surfaced` | A request for 10 seated passengers (over the 9-seat booking limit) makes the agent surface the limit conversationally instead of emitting a structure the schema would reject. | Authoring effort only. |
| fare_prep | `split_season_round_trip` | A December outbound with a January return derives *different* season codes per fare component - the per-leg derivation claim made in the top-level README, pinned end to end. | Authoring effort only; the derivation itself is already unit-tested in `tests/test_fare_request.py`. |
| fare_prep | `incomplete_intake_error_passthrough` | A gated intake (`ready_for_policy=false`) still produces exactly one tool call, and the tool's error passes through verbatim - the degradation path the agent is prompted for. | Authoring effort only. |
| policy | `garbled_quote_escalates` | A malformed or truncated engine response (rather than the clean failure text) must still be judged as "no usable quote": a no-argument `check_budget` call and `needs_review`. Extends the outage regression to messier failure shapes. | Authoring effort only. |
| summary_writer | `summary_faithfulness_rubric` | Rubric-graded prose: the summary states the decision and the real total, and invents no numbers. | Blocked on adopting ADK's rubric-based response metric, which needs its own config file (the metric raises on rubric-less cases, so it cannot live in the shared `test_config.json`). |
| orchestrator | `end_to_end_happy_path` | The full pipeline against the engine: one message in, a populated `PreTripApprovalOutput` out. | Blocked on an eval-context fare engine: a live A2A call makes the eval depend on an external service, so this needs a stub-vs-live decision first. |
| orchestrator | `incomplete_short_circuit` | An incomplete request stops at intake instead of running every stage. | Blocked on the conditional-edge gating feature (see Known gaps in the top-level README). |

Deliberate omission: there are no planned boundary cases (a fare of exactly $2000.00, a trip of exactly 14 days).
Threshold seams are deterministic code owned by `tests/test_policy.py`, and re-testing them through a model-in-the-loop eval would add cost and flake without adding coverage.

## Eval-driven workflow

All three evalsets are verified baselines: 13/13 cases pass on the Vertex AI path
(intake + fare_prep first verified 2026-07-07; policy authored and verified the
same day). CI re-runs them on every agent-shaping push (diffs touching
agents/, tools/, eval/, the gate, or deps - docs-only pushes skip the job and
still deploy; docs/DECISIONS.md §10) via
`tests/test_evals.py` (the `evals` job in `.github/workflows/deploy.yml`,
keyless Vertex AI auth via WIF) and gates deploy on them. If a run fails,
decide whether it's a regression (fix the agent) or reference drift (update
the evalset) — never both in one commit.

Local runs must set the same env CI uses (`GOOGLE_GENAI_USE_VERTEXAI=TRUE`,
`GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, with ADC) or an API key;
`tests/test_evals.py` fails fast otherwise, because a credential-less eval run
can report a vacuous pass.

fare_prep's reference `final_response` values are the deterministic translator's
own output (the agent's contract is to return the tool result verbatim), so they
can be regenerated any time by re-running `build_fare_request` with each case's
`tool_uses[0].args` and writing the JSON back into `final_response`.

policy's FareQuote fixtures are the fare engine's own deterministic output:
each case's trip was translated with `build_fare_request` (today=2026-07-07) and
priced with the engine's `fare.Calculate` (`travel-fare-engine` repo), so the
embedded quotes (including the $288.90 JFK-LAX journey that reproduces the
production-verified healthy path) are regenerable the same way.
Its reference responses embed days-in-advance numbers derived from the domain
clock; under `TRAVEL_CLOCK_TODAY=2026-07-07` (see Run above) they match the
tools' output exactly, on any calendar day.
The old mid-September-2026 date-refresh chore is retired: the fixed 2026-09
trip dates stay permanently valid because the frozen clock, not the wall
clock, drives `check_advance_purchase` and `build_fare_request`.

## Data hygiene

All traveler data in these sets is obviously fake ("Test Traveler",
`*@example.com`, employee `0000x`) - a non-negotiable project rule. Airport
codes are real IATA codes (not PII).
