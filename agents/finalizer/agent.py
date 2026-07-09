"""Finalizer: an LLM writes the prose; code assembles the record.

Split design (mirrors the engine's deterministic-core / LLM-shell thesis):

- `summary_writer` (LlmAgent) produces the one genuinely generative field,
  the 1-3 sentence human summary, into state via output_key. No
  output_schema, no tools.
- `finalizer_assembler` (FinalizerAssembler, no model) owns every structured
  field of PreTripApprovalOutput in pure Python - see assembler.py.

The previous single-LlmAgent finalizer transcribed the whole record through
the model; Gemini's schema-constrained decoding has no schema for the
untyped dicts nested inside (fare_quote), so nested fields like fare_rules
were probabilistically nulled or stringified in transit (production
invocation e-73f1abbe). Assembly in code removes that copy path instead of
supervising it.

`root_agent` stays a single importable node named "orchestrator_finalizer"
(now an ADK Workflow graph, formerly a SequentialAgent) so the orchestrator
pipeline and `adk run agents/finalizer` are unchanged.
"""

from google.adk.agents import LlmAgent
from google.adk.workflow import START, Workflow

from agents.model import gemini_flash

from .assembler import SUMMARY_STATE_KEY, FinalizerAssembler

SUMMARY_INSTRUCTION = """\
You are the approval summary writer, the last LLM step of the pre-trip
approval pipeline. Intake, the fare engine, and policy have already run.

Inputs from session state (template substitution):
- {intake_output} - the IntakeOutput JSON
- {policy_decision} - the PolicyDecision JSON
- The fare engine's FareQuote JSON is in the conversation history above (the
  object with `total_fare`, `quote_id`, and `taxes` fields); it is absent if
  the engine failed or could not price the trip.

Write a 1-3 sentence human summary of the outcome: the trip (route, dates),
the policy result and the reasons that drove it, and the total fare if a
quote exists. If intake is not ready_for_policy, say what is still missing
instead.

Output ONLY the summary sentences as plain text. No JSON, no markdown, no
headings. Do not restate raw data structures: a deterministic assembler owns
every structured field and attaches your summary verbatim.
"""

summary_writer = LlmAgent(
    name="summary_writer",
    model=gemini_flash,
    instruction=SUMMARY_INSTRUCTION,
    output_key=SUMMARY_STATE_KEY,
)

# A two-node Workflow graph replaces the deprecated SequentialAgent. The chain
# tuple (START, a, b) expands to the edges START -> summary_writer -> assembler;
# ADK agents are already graph nodes (BaseAgent subclasses BaseNode), so both
# stages drop in directly. The assembler, being the terminal node, owns the
# final approval record the workflow emits.
root_agent = Workflow(
    name="orchestrator_finalizer",
    edges=[(START, summary_writer, FinalizerAssembler(name="finalizer_assembler"))],
)
