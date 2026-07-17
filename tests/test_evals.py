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

from tools import clock

_ROOT = Path(__file__).resolve().parent.parent

# The references were authored on 2026-07-07 (commits db82710, 79b37e9): every
# baked day-count ("70 days in advance", advance_purchase_days) equals
# departure minus this date. Freezing the domain clock keeps them permanently
# valid; unset, they rot one token per day and hard-break once the 2026-09
# trip dates pass (docs/LESSONS.md lesson 16, docs/DECISIONS.md §8).
EVALSET_AUTHORING_DATE = "2026-07-07"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ADK_EVALS") != "1",
    reason="model-in-the-loop evals are opt-in: set RUN_ADK_EVALS=1",
)


@pytest.fixture(autouse=True)
def _require_model_credentials() -> None:
    """Fail loudly instead of passing vacuously when no model is reachable.

    Observed: with no credentials configured, the evaluator can report a pass
    without any real inference. Run these with the same env CI uses
    (GOOGLE_GENAI_USE_VERTEXAI=TRUE + project/location + ADC) or an API key.
    """
    has_creds = (
        os.environ.get("GOOGLE_GENAI_USE_VERTEXAI")
        or os.environ.get("GOOGLE_GENAI_USE_ENTERPRISE")
        or os.environ.get("GOOGLE_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
    )
    if not has_creds:
        pytest.fail(
            "RUN_ADK_EVALS=1 requires model credentials: set "
            "GOOGLE_GENAI_USE_VERTEXAI=TRUE with GOOGLE_CLOUD_PROJECT/"
            "GOOGLE_CLOUD_LOCATION (and ADC), or GOOGLE_API_KEY/GEMINI_API_KEY. "
            "A credential-less eval run is not meaningful."
        )


@pytest.fixture(autouse=True)
def _freeze_domain_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the domain clock to the evalset authoring date for every eval run."""
    monkeypatch.setenv(clock.ENV_VAR, EVALSET_AUTHORING_DATE)


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


@pytest.mark.asyncio
async def test_policy_evalset() -> None:
    from google.adk.evaluation.agent_evaluator import AgentEvaluator

    await AgentEvaluator.evaluate(
        agent_module="agents.policy.agent",
        eval_dataset_file_path_or_dir=str(_ROOT / "eval" / "policy.evalset.json"),
        num_runs=1,
    )
