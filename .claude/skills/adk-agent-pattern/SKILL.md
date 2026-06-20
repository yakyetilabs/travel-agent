name: adk-agent-pattern
description: Use whenever creating a new ADK agent file, adding tools to an existing agent, or refactoring agent code. Defines the canonical file structure, naming conventions, and the deterministic-tool rule for this repo.

---

# ADK agent pattern for this repo

Every agent directory must contain:

- `agent.py` — exposes a top-level `root_agent` so `adk run agents/<name>` works
- `schemas.py` — Pydantic models for inputs and outputs
- `prompts.py` — only if the system prompt is over ~30 lines; otherwise inline
- `__init__.py` — see canonical contents below

## Canonical **init**.py

`adk eval` loads the agent directory's `__init__.py` via
`importlib.util.spec_from_file_location`. That bypass means (a) the file must
explicitly expose the `agent` submodule (eval reads `agent_module.agent.root_agent`),
and (b) the repo root is not added to `sys.path`, so absolute imports like
`from tools.<x> import ...` or `from agents.<x>.agent import root_agent` fail.

Use this in every agent's `__init__.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from . import agent  # noqa: E402
```

`adk run` and `adk web` work without this, but `adk eval` does not.

## Canonical agent.py

IMPORTANT: `output_schema` and `tools` are mutually exclusive on an `LlmAgent`.
Setting `output_schema` disables tool calling (and sub-agent delegation) — see the
"ADK output_schema disables transfer_to_agent and tools" note in CLAUDE.md. Pick
the pattern that matches what the agent needs:

**Tool-calling agent (no structured output):** emit JSON-as-text and store it via
`output_key` for a downstream agent to consume. This is what `policy` and
`fare_prep` do.

```python
from google.adk.agents import LlmAgent
from tools.policy import check_budget, check_travel_class

root_agent = LlmAgent(
    name="policy_agent",
    model="gemini-2.5-pro",
    instruction="...",  # or import from prompts.py
    tools=[check_budget, check_travel_class],
    output_key="policy_decision",   # NOT output_schema — tools need to stay enabled
)
```

**Structured-output agent (no tools):** validate the final shape with
`output_schema`. This is what `intake` and `finalizer` do.

```python
from google.adk.agents import LlmAgent
from .schemas import AgentOutput

root_agent = LlmAgent(
    name="finalizer_agent",
    model="gemini-2.5-pro",
    instruction="...",
    output_schema=AgentOutput,
    output_key="agent_output",
)
```

## Tool rules

- Tools live in `tools/<domain>.py`, not inside agent directories.
- Tools are sync Python functions with type hints and docstrings.
- Tools never call LLMs.
- Tools return JSON-serializable dicts or Pydantic models.

## Naming

- Agent name (the `name=` kwarg) is snake_case and matches the directory name.
- Output schema class is `<AgentName>Output` in PascalCase.
