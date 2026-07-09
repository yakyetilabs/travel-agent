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

---

## 5. Explicit model retry budget, with an honest give-up at the boundary

**Decision.**
Define the Gemini model once (`agents/model.py`) with an explicit `HttpRetryOptions` retry budget - four attempts (one initial call plus three retries), exponential backoff of roughly 1s, 2s, 4s with full jitter, capped at 16s, over the transient status set (429, 408, and the retryable 5xx) - and import that single instance into every LLM agent (`intake`, `fare_prep`, `policy`, `summary_writer`).
When the budget is exhausted the serving layer (`model_errors.py`) translates the underlying `google.genai.errors.APIError` into a structured, retryable HTTP response: `503 {"error": "model_busy", "retryable": true}` for a 429, and `503 {"error": "model_unavailable", ...}` for a retryable 5xx, rather than letting it surface as a 500 stack trace.

**Context.**
Gemini 2.5 Flash runs under Vertex AI's dynamic shared quota and has no dedicated capacity, so a `429 RESOURCE_EXHAUSTED` is transient regional contention, not a client error.
Passing the bare string `model="gemini-2.5-flash"` leaves `retry_options` unset, and google-genai then configures its retry to stop after a single attempt (`retry_args(None)` maps to `stop_after_attempt(1)`), so there is effectively no retry and one blip failed the whole run with a stack trace (observed 2026-07-08: a 429 at intake).
Retrying at the model layer, from one shared definition, means a blip is absorbed inside an agent's turn and every agent inherits the same policy from a single source of truth.

**Rejected alternatives.**
- Per-agent retry configuration.
  Rejected: four copies of the same policy drift out of sync; one shared model instance is the single knob.
- Degrading an exhausted 429 to a `needs_review` approval record.
  Rejected as dishonest: a 429 at intake means there is no data to judge, so a manufactured verdict would misrepresent an infrastructure outage as a policy outcome (the same stance decision 3 takes on unreadable input, which never becomes an approval).
  The caller gets a retryable error instead.
- Rewriting the streaming (`/run_sse`) error into the same envelope.
  Rejected for coupling: ADK's SSE handler already catches exceptions inside the stream and emits them as an inline `{"error": ...}` event after the 200 response has started, so a FastAPI exception handler cannot restyle that status.
  The Dev UI therefore already shows an error event rather than a stack trace; matching the envelope there would mean re-implementing ADK's route for little gain, so the structured 503 covers the non-streaming `/run` path that programmatic callers use.

**Consequences.**
Transient contention is now invisible to the caller within the retry budget, and a sustained outage returns a clean, retryable 503 with a `Retry-After` hint, never a stack trace and never a fabricated approval.
The budget is deliberately conservative at four attempts: the value is explicit 429 coverage plus real backoff and jitter, not the attempt count, so it can be raised if 429s recur.
The fare engine's inbound LLM has the same exposure and is tracked as a separate follow-up in the engine repository; the orchestrator half lands here.
The behavior is pinned in `tests/test_model_resilience.py` (the retry configuration, every agent sharing it, recover-on-transient and give-up-on-persistent, and the 503 envelope), and the reader-facing gap in `README.md` is updated to reflect the orchestrator half being closed.

---

## 6. External reviewer access via IAP enabled directly on Cloud Run

**Decision.**
Give external reviewers access to the private `travel-prequal` service by enabling Identity-Aware Proxy (IAP) directly on the Cloud Run service, rather than through an external HTTPS load balancer or a custom-built front-end.
Personal Google accounts are admitted through a custom OAuth client on an External consent screen, each reviewer is granted `roles/iap.httpsResourceAccessor` individually, and the surface they reach is the ADK Dev UI.

**Context.**
The service runs private (`--no-allow-unauthenticated`), so a portfolio demo needs a link a reviewer can open with their own Google identity, without the project minting credentials or building a sign-in.
IAP provides that directly: Google's sign-in is the identity proof, and IAP checks it against an IAM allowlist before a request reaches the container, in front of every ingress path including the default `run.app` URL, so there is no unprotected side door.

**Rejected alternatives.**
- An external HTTPS load balancer with IAP on the backend service.
  Rejected: it adds a serving component to operate for no capability that IAP directly on Cloud Run lacks.
- A custom front-end gated by a shared access code.
  Rejected: it moves authentication into code we would have to build and secure, and one shared code is a weaker control than per-identity Google sign-in.

**Consequences.**
Admitting external Gmail requires the consent screen set to External with a custom OAuth Web client, because the default Google-managed IAP client only admits accounts inside the project's organization, and this project has none.
Reviewers are pre-provisioned, and the friction is deliberate: each email must be a Test user on the consent screen and hold `roles/iap.httpsResourceAccessor`, matching the exact account the person signs in with - the accepted trade for not building and defending a public front door.
Verified end to end on 2026-07-08: an external Gmail reviewer signed in, reached the Dev UI, and ran the pipeline to an approved decision with a live fare quote, the agent events streaming in progressively, which confirms server-sent events are neither buffered nor broken by IAP.

---

## 7. The pipeline is an ADK Workflow graph, not a SequentialAgent

**Decision.**
Build both pipelines - the five-stage orchestrator and the two-stage finalizer - as ADK `Workflow` graphs rather than `SequentialAgent`s.
Each stays a single linear chain expressed as one tuple from the `START` sentinel, and every existing stage is reused unchanged as a graph node.

**Context.**
ADK deprecates `SequentialAgent` in favor of `Workflow`, and the warning fires on every module load and every test run, which is visible noise in a project meant to read as polished.
The standing rule is to move to the supported replacement rather than to silence the warning, so the question was only how large the move is.
Read against the installed ADK source, `Workflow` is not a renamed `SequentialAgent` but a different engine: a graph of nodes and edges driven by a scheduler, where `SequentialAgent` was a fixed list run in order.
The migration is small anyway, because `BaseAgent` now subclasses the graph's `BaseNode`, so every stage this pipeline already has - the intake, fare-prep and policy `LlmAgent`s, the remote fare engine, and the model-free assembler - is already a node and drops into a graph with no wrapper.
A one-element chain tuple `(START, a, b, ...)` expands to the linear edges the old `sub_agents` list implied, so the graph still reads in pipeline order.
The load-bearing entry points are unchanged: the ADK Runner, the `get_fast_api_app` agent loader that serves the Dev UI, and `adk run` all accept a `Workflow` root, and `LlmAgent` nodes still honor `output_key` and instruction state-templating, so the state hand-off the pipeline relies on is preserved.

**Rejected alternatives.**
- Silence the `DeprecationWarning` with a pytest or warnings filter.
  Rejected: it hides the blemish instead of adopting the supported API and leaves the core of the system on a path the library has slated for removal.
- Stay on `SequentialAgent` until it is actually removed.
  Rejected: the pipeline is a plain linear chain today, which is the simplest it will ever be to move, and the warning is a quality signal a reviewer sees now.
- Wire the graph from explicit `Edge(from, to)` objects.
  Rejected as ceremony for a linear pipeline; the chain tuple is the library's intended shorthand and reads as the sequence itself, and explicit edges remain available the day the pipeline grows a branch.

**Consequences.**
The orchestration is a graph rather than a list: the stages are `root_agent.graph.nodes`, not `root_agent.sub_agents`, and the finalizer is a nested `Workflow` that is itself the orchestrator's terminal node, so the deterministic assembler stays the last thing the pipeline emits - the property §4 exists to guarantee.
Running the engine as a graph node changed the shape of its result in the event stream, and this is the one real hazard the migration surfaced: the graph models the remote `fare_engine` round trip as a `compute_fare` tool call, so the FareQuote now arrives as a function response rather than the model text part the flat pipeline produced.
The deterministic assembler reads the quote from the fare_engine event, so it now accepts either shape (`agents/finalizer/assembler.py`), and both are pinned in `tests/test_finalizer_assembler.py`; without that, `fare_quote` would serialize as null in production even though every unit test passed, which is exactly what the end-to-end run caught and the shape-blind tests did not.
Verified end to end after the migration: the standard JFK-LAX query runs through the graph to an approved decision carrying the full $288.90 quote with `fare_rules` populated on both components, matching the result the SequentialAgent produced, and the deprecation warning is gone from module load and from the test suite.
