"""Model resilience: the retry budget (agents/model.py) and the give-up handler
(model_errors.py) that together turn a transient Vertex 429 from a fatal stack
trace into either a silent recovery or an honest, retryable 503.

No network or Vertex access: we assert the retry CONFIG and drive google-genai's
own ``retry_args`` with a fake call, and we mount the real HTTP handler on a
minimal FastAPI app.
"""

import pytest
import tenacity
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.genai._api_client import retry_args  # the fn google-genai itself uses
from google.genai.errors import ClientError, ServerError

from agents.fare_prep.agent import root_agent as fare_prep_agent
from agents.finalizer.agent import summary_writer
from agents.intake.agent import root_agent as intake_agent
from agents.model import gemini_flash
# policy's root is now a model-free Workflow (LLM half + assembler, like the
# finalizer); the retry contract applies to its inner LlmAgent.
from agents.policy.agent import policy_checks
from model_errors import install_model_error_handler


def _fake_api_error(cls, code, status):
    """Build a google-genai error the way the SDK would, without a network call.

    APIError(code, response_json, response=None); response_json carries the
    nested {"error": {"status": ...}} shape the SDK parses.
    """
    return cls(code, {"error": {"code": code, "status": status, "message": "x"}})


# --------------------------------------------------------------------------- #
# Retry budget (agents/model.py)
# --------------------------------------------------------------------------- #


def test_retry_options_are_explicit_and_cover_429():
    opts = gemini_flash.retry_options
    assert opts is not None
    assert opts.attempts == 4
    assert opts.initial_delay == 1.0
    assert opts.exp_base == 2.0
    assert opts.max_delay == 16.0
    assert opts.jitter == 1.0
    assert 429 in opts.http_status_codes


def test_every_pipeline_llm_agent_uses_the_shared_retry_model():
    # Regression guard: reverting any agent to the bare string "gemini-2.5-flash"
    # silently disables retry (see the single-attempt test below) and must fail
    # here.
    for agent in (intake_agent, fare_prep_agent, policy_checks, summary_writer):
        assert agent.model is gemini_flash


def test_unset_retry_options_would_be_a_single_attempt():
    # The exact failure mode being fixed: the SDK maps "no retry_options" to a
    # single attempt, so one transient 429 kills the run.
    assert retry_args(None)["stop"].max_attempt_number == 1
    assert retry_args(gemini_flash.retry_options)["stop"].max_attempt_number == 4


def test_transient_429_recovers_within_the_budget():
    args = {**retry_args(gemini_flash.retry_options), "wait": tenacity.wait_none()}
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise _fake_api_error(ClientError, 429, "RESOURCE_EXHAUSTED")
        return "ok"

    assert tenacity.Retrying(**args)(flaky) == "ok"
    assert calls["n"] == 2  # one failure, then recovery on the retry


def test_persistent_429_gives_up_after_the_budget():
    args = {**retry_args(gemini_flash.retry_options), "wait": tenacity.wait_none()}
    attempts = {"n": 0}

    def always_429():
        attempts["n"] += 1
        raise _fake_api_error(ClientError, 429, "RESOURCE_EXHAUSTED")

    with pytest.raises(ClientError):
        tenacity.Retrying(**args)(always_429)
    assert attempts["n"] == 4  # initial + 3 retries, then re-raise


# --------------------------------------------------------------------------- #
# Give-up handler (model_errors.py)
# --------------------------------------------------------------------------- #


def _client_raising(exc: Exception) -> TestClient:
    app = FastAPI()
    install_model_error_handler(app)

    @app.post("/boom")
    async def boom():  # pragma: no cover - body never returns
        raise exc

    # raise_server_exceptions=False so an unmatched handler yields a 500 response
    # to assert on rather than propagating out of the test client.
    return TestClient(app, raise_server_exceptions=False)


def test_exhausted_429_becomes_model_busy_503():
    resp = _client_raising(
        _fake_api_error(ClientError, 429, "RESOURCE_EXHAUSTED")
    ).post("/boom")
    assert resp.status_code == 503
    assert resp.json() == {"error": "model_busy", "retryable": True}
    assert resp.headers["Retry-After"] == "5"


@pytest.mark.parametrize("code", [500, 502, 503, 504])
def test_transient_5xx_becomes_model_unavailable_503(code):
    resp = _client_raising(_fake_api_error(ServerError, code, "UNAVAILABLE")).post(
        "/boom"
    )
    assert resp.status_code == 503
    assert resp.json() == {"error": "model_unavailable", "retryable": True}


def test_nonretryable_model_error_is_structured_not_a_stack_trace():
    resp = _client_raising(
        _fake_api_error(ClientError, 400, "INVALID_ARGUMENT")
    ).post("/boom")
    assert resp.status_code == 400
    assert resp.json() == {"error": "model_error", "retryable": False}


def test_429_never_degrades_to_an_approval():
    resp = _client_raising(
        _fake_api_error(ClientError, 429, "RESOURCE_EXHAUSTED")
    ).post("/boom")
    assert resp.status_code >= 500
    assert "approv" not in resp.text.lower()
