"""Serving-layer give-up handler for exhausted model retries.

``agents/model.py`` gives every ``LlmAgent`` an explicit retry budget for
transient Vertex 429s. When that budget is exhausted (or a retryable 5xx runs
out too), google-genai raises ``google.genai.errors.APIError`` and ADK re-raises
it unchanged, so it reaches the FastAPI serving layer. Without a handler that is
a 500 with a stack trace on the ``/run`` endpoint.

This module translates it into an honest, structured response:

- 429 RESOURCE_EXHAUSTED            -> 503 {"error": "model_busy",        "retryable": true}
- retryable 5xx (500/502/503/504)   -> 503 {"error": "model_unavailable", "retryable": true}
- any other API error               -> its own status code, {"error": "model_error", "retryable": false}

We deliberately do NOT fabricate an approval-style record: a 429 at intake means
there is no data to judge, so degrading to a needs_review verdict would be
dishonest (the same stance the finalizer takes on unreadable input). The caller
gets a retryable error and can try again.

Streaming caveat: this covers the non-streaming ``/run`` endpoint only. ADK's
``/run_sse`` handler catches exceptions inside the stream and emits them as an
inline ``{"error": ...}`` SSE event after the 200 response has already begun, so
a FastAPI exception handler cannot rewrite that status. The Dev UI therefore
already degrades to a visible error event rather than a stack trace; matching
this envelope there would mean re-implementing ADK's route. See docs/DECISIONS.md.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from google.genai.errors import APIError

logger = logging.getLogger(__name__)

# Model API status codes we treat as transient / retryable at the boundary.
_RATE_LIMIT_STATUS = 429
_TRANSIENT_SERVER_STATUS = frozenset({500, 502, 503, 504})
# A modest client-facing backoff hint (seconds) for the retryable responses.
_RETRY_AFTER_SECONDS = "5"


async def model_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Translate a transient/exhausted model API error into a structured HTTP
    response instead of a 500 stack trace."""
    if exc.code == _RATE_LIMIT_STATUS:
        # Retries are logged by google-genai; this records that the budget was
        # exhausted and we are degrading to a retryable 503.
        logger.warning("Model rate limit exhausted (429 %s); returning model_busy 503.", exc.status)
        return JSONResponse(
            status_code=503,
            content={"error": "model_busy", "retryable": True},
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        )
    if exc.code in _TRANSIENT_SERVER_STATUS:
        logger.warning("Transient model error %s exhausted; returning model_unavailable 503.", exc.code)
        return JSONResponse(
            status_code=503,
            content={"error": "model_unavailable", "retryable": True},
            headers={"Retry-After": _RETRY_AFTER_SECONDS},
        )
    # Non-transient model error (e.g. 400/403): surface it honestly - still
    # structured, no stack trace - and mark it non-retryable so a client does
    # not loop on a request that will never succeed. Logged at error with the
    # traceback so a genuine request/config bug is not swallowed silently.
    logger.error("Non-transient model API error (%s); returning structured error.", exc.code, exc_info=exc)
    return JSONResponse(
        status_code=exc.code or 500,
        content={"error": "model_error", "retryable": False},
    )


def install_model_error_handler(app: FastAPI) -> None:
    """Register the model-error handler on the ADK FastAPI app.

    Registered for the base ``APIError``; Starlette resolves by the exception's
    MRO, so this also covers ``ClientError``/``ServerError`` subclasses and wins
    over any broader ``Exception`` handler.
    """
    app.add_exception_handler(APIError, model_error_handler)
