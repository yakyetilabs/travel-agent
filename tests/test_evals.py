"""CI gate for the ADK evalsets (model-in-the-loop).

The `adk eval` CLI prints a pass/fail summary but always exits 0, so it cannot
gate a pipeline (verified against google-adk 2.3.0 `cli_tools_click.py`: the
eval command prints the summary and returns).
This module wraps the same evalsets in `AgentEvaluator`, which asserts on
failures, so the CI `evals` job actually fails the build on a regression.

These are opt-in (CLAUDE.md: no live-API tests in CI without a flag): set
RUN_ADK_EVALS=1 to run them, as the workflow's `evals` job does.
They also need the eval extra (`uv sync --extra eval`) and model credentials.
Unlike the CLI, `AgentEvaluator` auto-discovers `eval/test_config.json` from
the evalset's folder, so no explicit config path is needed here.
"""

import os
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ADK_EVALS") != "1",
    reason="model-in-the-loop evals are opt-in: set RUN_ADK_EVALS=1",
)


@pytest.mark.asyncio
async def test_intake_evalset() -> None:
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    # agent_module is a dotted import path (AgentEvaluator uses importlib),
    # not a filesystem path like the CLI takes.
    await AgentEvaluator.evaluate(
        agent_module="agents.intake.agent",
        eval_dataset_file_path_or_dir=str(_ROOT / "eval" / "intake.evalset.json"),
        num_runs=1,
    )


@pytest.mark.asyncio
async def test_fare_prep_evalset() -> None:
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    await AgentEvaluator.evaluate(
        agent_module="agents.fare_prep.agent",
        eval_dataset_file_path_or_dir=str(_ROOT / "eval" / "fare_prep.evalset.json"),
        num_runs=1,
    )
