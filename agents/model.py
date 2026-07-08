"""The one Gemini model every orchestrator agent shares, with an explicit
retry budget for Vertex AI's dynamic shared quota.

Why this module exists
----------------------
gemini-2.5-flash has no dedicated capacity: it runs under Vertex's dynamic
shared quota, so under regional contention a call is rejected with
``429 RESOURCE_EXHAUSTED`` - transient, not a client error. Passing the bare
string ``model="gemini-2.5-flash"`` leaves ``retry_options`` unset, and
google-genai then maps that to ``stop_after_attempt(1)`` - a single try, no
backoff - so one blip fails the whole run with a stack trace (observed
2026-07-08: a 429 at intake).

Defining the model ONCE here, with explicit ``HttpRetryOptions``, and importing
this single instance into every ``LlmAgent`` gives the whole pipeline real
exponential backoff with jitter from one source of truth. Retrying at the model
(lowest) layer means a blip is absorbed inside an agent's turn instead of
surfacing to the caller.

Config rationale (``attempts`` counts the initial call, so 4 = 1 try + 3 retries):

- ``attempts=4``           conservative budget; the win is explicit 429 coverage
                           plus real backoff and jitter, not the count. Raise if
                           429s recur.
- ``initial_delay``/``exp_base``/``max_delay``
                           ~1s, 2s, 4s between retries, capped at 16s.
- ``jitter=1.0``           full jitter (a float factor fed to tenacity's
                           ``wait_exponential_jitter``), so concurrent agents do
                           not retry in lockstep and re-collide.
- ``http_status_codes``    the transient set: 429 plus 408 and the retryable 5xx.

When the budget is exhausted the underlying ``google.genai.errors.APIError``
propagates unchanged (ADK re-raises it); the serving layer turns that into a
structured 503 - see ``model_errors.py``.
"""

from google.adk.models import Gemini
from google.genai import types

gemini_flash = Gemini(
    model="gemini-2.5-flash",
    retry_options=types.HttpRetryOptions(
        attempts=4,
        initial_delay=1.0,
        exp_base=2.0,
        max_delay=16.0,
        jitter=1.0,
        http_status_codes=[408, 429, 500, 502, 503, 504],
    ),
)
