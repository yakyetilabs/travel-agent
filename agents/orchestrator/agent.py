import base64
import json
import logging
import os
import time
from collections.abc import Generator
from urllib.parse import urlparse

import google.auth.transport.requests
import httpx
from google.adk.agents import LlmAgent, SequentialAgent
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent
from google.genai import types
from google.oauth2 import id_token as oauth_id_token

from agents.fare_prep.agent import root_agent as fare_prep_root
from agents.finalizer.agent import root_agent as finalizer_root
from agents.intake.agent import root_agent as intake_root
from agents.policy.agent import root_agent as policy_root

logger = logging.getLogger(__name__)


class _GCPIdTokenAuth(httpx.Auth):
    _REFRESH_LEEWAY_SEC = 60

    def __init__(self, audience: str) -> None:
        self._audience = audience
        self._auth_req = google.auth.transport.requests.Request()
        self._token: str | None = None
        self._expiry: float = 0.0

    def _refresh(self) -> None:
        self._token = oauth_id_token.fetch_id_token(self._auth_req, self._audience)
        payload_b64 = self._token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        self._expiry = float(json.loads(base64.urlsafe_b64decode(payload_b64))["exp"])

    def auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        if (
            self._token is None
            or time.time() >= self._expiry - self._REFRESH_LEEWAY_SEC
        ):
            self._refresh()
        request.headers["Authorization"] = f"Bearer {self._token}"
        yield request


_fare_engine_url = os.environ["FARE_ENGINE_URL"]


def _is_local(url: str) -> bool:
    """True if the URL targets the local machine, where the engine runs
    unauthenticated and no Cloud Run ID token can (or should) be minted."""
    return urlparse(url).hostname in ("localhost", "127.0.0.1", "::1", "[::1]")

# Local dev hits an unauthenticated engine, so skip ID-token auth (fetching one
# would require GCP credentials and fail). Deployed engines run
# --no-allow-unauthenticated, so every call carries an ID token.
# Read timeout matches the engine's Cloud Run request timeout (120s): the engine's
# inbound LLM call can be slow, and abandoning a request the engine will still
# complete wastes the quote (observed: a 66s engine response vs a 60s client cap).
_timeout = httpx.Timeout(120.0, connect=10.0)
if _is_local(_fare_engine_url):
    logger.info("FARE_ENGINE_URL is local (%s); calling without ID-token auth", _fare_engine_url)
    _client = httpx.AsyncClient(timeout=_timeout)
else:
    _client = httpx.AsyncClient(
        auth=_GCPIdTokenAuth(audience=_fare_engine_url),
        timeout=_timeout,
    )

fare_engine = RemoteA2aAgent(
    name="fare_engine",
    agent_card=f"{_fare_engine_url}/.well-known/agent-card.json",
    httpx_client=_client,
)

# Pipeline order matters: fare_prep derives the engine's request from intake, the
# remote fare_engine prices it, and ONLY THEN does policy run — so the budget check
# can act on the real quoted total_fare instead of guessing before pricing exists.
root_agent = SequentialAgent(
    name="orchestrator_agent",
    sub_agents=[intake_root, fare_prep_root, fare_engine, policy_root, finalizer_root],
)
