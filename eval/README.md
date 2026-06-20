# Evals

ADK evalsets for the orchestrator's agents. These are the **end-to-end / model-in-the-loop**
half of the system's evaluation story; the fare engine's deterministic pricing is
pinned separately by the Go eval harness in the `travel-fare-engine` repo.

## Run

```bash
adk eval agents/intake    eval/intake.evalset.json
adk eval agents/fare_prep eval/fare_prep.evalset.json
```

Thresholds live in [`test_config.json`](test_config.json):

- `tool_trajectory_avg_score` (1.0) — did the agent call the right tools with the
  right args, in order? Exact match.
- `response_match_score` (0.5) — ROUGE overlap of the final response against the
  reference. Lenient on purpose (see caveat below).

## What each set checks

- **`intake.evalset.json`** — natural language → structured `IntakeOutput`. Intake
  has no tools, so grading is purely response match. Includes a gated case
  (missing fields → `ready_for_policy=false`) and complete cases.
- **`fare_prep.evalset.json`** — reads `{intake_output}` from `session_input.state`
  and must call `build_fare_request` exactly once with the trip's
  origin/destination/date/class/passengers. Graded primarily on the **tool
  trajectory**, which is deterministic. The *derived* `fare_request` (distance,
  booking class, season, advance-purchase days) depends on the run date, so the
  reference `final_response` is illustrative and `response_match` is lenient.

## Eval-driven workflow (see ../CLAUDE.md)

These files are committed **baselines**, not verified pass rates — they were
authored against the ADK 2.0 evalset schema but have not been run here (no model
credentials in this environment). On first run, capture the baseline, then for any
non-trivial prompt/tool/model change: re-run, and if the pass rate drops decide
whether it's a regression (fix the agent) or reference drift (update the evalset) —
never both in one commit.

## Data hygiene

All traveler data in these sets is obviously fake ("Test Traveler",
`*@example.com`, employee `0000x`) per the CLAUDE.md non-negotiable rule. Airport
codes are real IATA codes (not PII).
