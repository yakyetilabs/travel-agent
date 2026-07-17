"""The domain clock: "today" as an explicit, injectable dependency.

The travel domain derives values from the current date (advance-purchase days,
season windows), which makes tool output - and the eval references that pin
it - a function of the wall clock. This seam exists so evals can freeze the
DOMAIN's clock to the date their references were authored, without freezing
the whole process (google-auth token expiry, asyncio timers) the way
freezegun-style libraries do. See docs/DECISIONS.md §8.

Production behavior is unchanged: with TRAVEL_CLOCK_TODAY unset (or empty),
`today()` is the real calendar date. A malformed value raises ValueError
loudly - a silently ignored override would un-freeze every eval reference at
once.
"""

import os
from datetime import date

ENV_VAR = "TRAVEL_CLOCK_TODAY"


def today() -> date:
    """Return the domain's current date, honoring the TRAVEL_CLOCK_TODAY override (ISO YYYY-MM-DD)."""
    frozen = os.environ.get(ENV_VAR)
    if not frozen:
        return date.today()
    return date.fromisoformat(frozen)
