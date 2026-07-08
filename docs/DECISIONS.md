# Travel Agent (Orchestrator) - Architecture Decision Record (ADR)

Decisions we chose to make while building the orchestrator, with the reasoning and the alternatives we rejected.

This is the decisions-genre companion to [`LESSONS.md`](LESSONS.md).
The split is deliberate: LESSONS records problems we ran into and did not foresee (retrospective); this file records choices we made on purpose (prospective), so the "why" behind the architecture has a home that is not a commit message.
The pricing engine keeps its own ADR in the sibling repository (`travel-fare-engine/DECISIONS.md`); several decisions here are the orchestrator-side half of a decision recorded there, and are cross-referenced where that is the case.

Reader docs - this file, [`LESSONS.md`](LESSONS.md), [`ARCHITECTURE.md`](ARCHITECTURE.md), and the top-level `README.md` - describe the system for humans and cross-reference each other.

---

## 1. Sequence policy after pricing

**Decision.**
Order the `SequentialAgent` pipeline `intake → fare_prep → fare_engine → policy → finalizer`, so the policy agent runs only after a real `FareQuote` exists.

**Context.**
The budget rule compares the trip's total against a cap.
In an earlier ordering, policy ran before the fare engine, so `total_fare` did not exist yet and the budget check was a stub that always passed.
A deterministic check that cannot see the value it guards is not a check.

**Rejected alternatives.**
- Estimate the fare inside policy so it can run earlier.
  Rejected: an estimate is a second, divergent pricing path that can disagree with the engine, which is exactly the ground truth the budget rule must act on.
- Keep the budget check as a soft advisory and reconcile later.
  Rejected: it lets an over-budget trip reach an "approved" state before the real number lands.

**Consequences.**
Sequence is part of the design, not incidental.
Each stage is placed so the inputs its tools require already exist in session state.
This is the orchestrator-side statement of the same "deterministic check needs a value computed later" lesson recorded in `LESSONS.md` (lesson 7).

---

## 2. `output_schema` versus tools shapes the agent topology

**Decision.**
Never give a single `LlmAgent` both `tools=[...]` and `output_schema=...`.
Agents that must call tools (`fare_prep`, `policy`) emit JSON-as-text and write to state via `output_key`; structured output is produced by a separate step.

**Context.**
In ADK, setting `output_schema` puts an `LlmAgent` into structured-output mode, which disables both tool calling and `transfer_to_agent` (verified against the installed ADK source, not memory).
The trace symptom is an orchestrator that stops after its first sub-agent and never delegates.
This constraint, not preference, is why the pipeline is a chain of single-responsibility agents rather than one agent that does everything.

**Rejected alternatives.**
- One finalizer `LlmAgent` with both tools and `output_schema`.
  Rejected: it silently produces structured output but never calls its tools.

**Consequences.**
The topology is shaped by the constraint: tool-users speak JSON-as-text; a dedicated final step owns the typed output.
Decision 4 takes this further and removes the LLM from the final step entirely.
See also `LESSONS.md` lesson 4.

---

## 3. Three-way policy verdicts with thresholds as code constants

**Decision.**
Every policy check returns one of three verdicts - `pass`, `needs_approval`, or `fail` - and the overall status is derived deterministically: any `fail` denies the trip; otherwise any `needs_approval` escalates it to `needs_review` with `requires_manager_approval=True`; otherwise it is approved.
All thresholds (the $2000 trip budget cap, allowed cabins, the 7-day advance-purchase minimum, the 14-day duration limit) are module constants in `tools/policy.py`, taken as no tool parameter.
Landed in commit `4b02042`.

**Context.**
Real pre-trip approval escalates out-of-policy requests to a manager rather than flat-denying everything unusual; a binary allow/deny cannot express "allowed with sign-off".
Keeping thresholds as constants rather than tool arguments means the LLM decides only *when* to call a tool and can never pass - and therefore never weaken - a limit.
Two failure modes are closed on purpose: a check that cannot run (no fare quote) escalates instead of passing, and a malformed tool result (missing `verdict` key) counts as a `fail` rather than weakening the decision.

**Rejected alternatives.**
- Binary approve/deny.
  Rejected: it forces a manager-approvable business-class trip into either a false approval or an unwarranted denial.
- Thresholds passed as tool arguments (or applied in the prompt).
  Rejected: it puts a policy number on the LLM's side of the boundary, where it can be misremembered or overridden.
- Treat a missing fare quote as a pass, or a malformed result as non-blocking.
  Rejected: a mandatory check that cannot run must never count as a pass; garbage must never weaken a policy verdict.

**Consequences.**
`agents/policy/rules.py` is the single canonical decision rule, cited by the policy prompt so prompt and code cannot describe different logic.
The no-fare-escalates behavior is the orchestrator-side complement to the engine's inbound-only LLM: when pricing is unavailable, the trip escalates and can never be approved with its budget unverified.
The policy evalset pins this in CI.

---

## 4. Deterministic finalizer: the LLM writes prose, code assembles structure

**Decision.**
Split the finalizer into two sub-agents:
`summary_writer` (an `LlmAgent`) writes only the 1-3 sentence human summary to state, and `finalizer_assembler` (a custom `BaseAgent` with no model) assembles `PreTripApprovalOutput` in pure Python from session state plus the current invocation's `fare_engine` event.
The `fare_quote` is copied verbatim from the engine's response; `final_decision` is derived entirely in code.
`root_agent` remains a single importable `SequentialAgent` named `orchestrator_finalizer`, so the orchestrator pipeline and `adk run agents/finalizer` are unchanged.

**Context.**
The previous finalizer was one `LlmAgent` with `output_schema` that re-emitted every field of the record, including the untyped `fare_quote` dict.
Gemini's schema-constrained decoding has no schema for the objects nested inside a bare `dict`, so nested fields were transcribed probabilistically: in production (invocation e-73f1abbe) `fare_components[*].fare_rules` arrived populated from the engine and left the finalizer as `null`; a local reproduction instead retyped the same objects as JSON strings.
Either way the approval record silently lost material information (refundability, changeability), and the model burned ~1200 thought tokens on what is a copy job.
The summary is the one genuinely generative field in the output; everything else already exists as data upstream.

**Rejected alternatives.**
- Callback-patching the existing `LlmAgent` (an after-model or after-agent callback that overwrites the lost fields).
  Rejected: the LLM still transcribes everything first, so it pays the tokens and keeps the failure mode, and the trace then shows the model saying one thing while the pipeline outputs another.
  Never ask a question you intend to ignore.
- Prompt-hardening ("really, copy verbatim").
  Rejected: transcription through a probabilistic channel stays probabilistic; the production trace is the counterexample to instruction-following here.

**Consequences.**
The custom agent removes the copy path instead of supervising it: zero transcription variance, fewer tokens, and honest traces where the served record is a pure function of upstream data.
Extraction is scoped to the current invocation's `fare_engine` event, so a stale quote from an earlier turn in the same session can never leak into a later approval record.
Unreadable policy output degrades to `needs_review` (never an approval), mirroring the malformed-counts-as-fail stance of decision 3.
This completes the "deterministic core, LLM shell" thesis end to end: it is the same cure the pricing engine applied to its own outbound path when it replaced a formatter LLM with a deterministic passthrough (engine repo `DECISIONS.md` §6), now applied on the orchestrator side.
The assembly logic is pure functions unit-tested in `tests/test_finalizer_assembler.py`; the lesson behind the bug is recorded in `LESSONS.md` lesson 8.

**Measured impact.**
Verified in production on 2026-07-08 against the deployed orchestrator, three happy-path runs through the authenticated proxy.
The finalizer stage fell from ~11.83s (the previous single-`LlmAgent` transcription) to 3.11s on average.
Of that, the pure-Python `finalizer_assembler` contributes 0.00s (sub-10ms); the residual ~3.1s is entirely `summary_writer`'s short prose call, which is the only generative work left.
End-to-end latency dropped from ~30s to ~20s, and because the finalizer accounted for nearly the whole gap, removing the LLM copy path recovered most of the pipeline's latency as a side effect of the correctness fix.
The same run confirmed `fare_components[*].fare_rules` now arrives as a populated object rather than `null`, closing the production bug that motivated the decision.
