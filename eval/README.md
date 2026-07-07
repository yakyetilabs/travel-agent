# Evals

ADK evalsets for the orchestrator's agents. These are the **end-to-end / model-in-the-loop**
half of the system's evaluation story; the fare engine's deterministic pricing is
pinned separately by the Go eval harness in the `travel-fare-engine` repo.

## Run

```bash
adk eval agents/intake    eval/intake.evalset.json    --config_file_path eval/test_config.json
adk eval agents/fare_prep eval/fare_prep.evalset.json --config_file_path eval/test_config.json
```

The `--config_file_path` flag is required: the `adk eval` CLI does **not**
auto-discover `test_config.json` from the evalset's folder (only the pytest
`AgentEvaluator` API does). Without it the CLI silently grades with the ADK
defaults (`response_match_score: 0.8`) instead of our deliberate thresholds.

Thresholds live in [`test_config.json`](test_config.json):

- `tool_trajectory_avg_score` (1.0) — did the agent call the right tools with the
  right args, in order? Exact match.
- `response_match_score` (0.5) — ROUGE overlap of the final response against the
  reference. Lenient on purpose (see caveat below).

## What each set checks

- **`intake.evalset.json`** — natural language → structured `IntakeOutput`. Intake
  has no tools, so grading is purely response match. Includes a gated case
  (missing fields → `ready_for_policy=false`), a one-way case, and complete
  round-trip cases; `trip_type` must be inferred only when the traveler is
  explicit.
- **`fare_prep.evalset.json`** — reads `{intake_output}` from `session_input.state`
  and must call `build_fare_request` exactly once with the trip's
  origin/destination/trip_type/dates/class/passengers (omitting `return_date`
  for one-way trips). Graded primarily on the **tool trajectory**, which is
  deterministic. The *derived* `fare_request` (per-leg fare components: distance,
  booking class, season, advance-purchase days) depends on the run date, so the
  reference `final_response` is illustrative and `response_match` is lenient.

## Eval-driven workflow

These files are committed **baselines**, not verified pass rates — they were
authored against the ADK 2.0 evalset schema but have not been run here (no model
credentials in this environment). CI runs both evalsets (the `evals` job in
`.github/workflows/deploy.yml`, keyless Vertex AI auth via WIF) and gates deploy
on them; the first CI run is the verified baseline. If a run fails, decide
whether it's a regression (fix the agent) or reference drift (update the evalset) —
never both in one commit.

## Data hygiene

All traveler data in these sets is obviously fake ("Test Traveler",
`*@example.com`, employee `0000x`) - a non-negotiable project rule. Airport
codes are real IATA codes (not PII).
