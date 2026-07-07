"""Reference implementation of the policy decision rule.

The policy agent's prompt cites this module as the canonical decision logic so
the prompt and code can't describe different rules. `tool_results` maps each tool
name to its returned dict (which carries a `verdict` of "pass", "needs_approval",
or "fail").
"""

from .schemas import Status


def _verdicts(tool_results: dict) -> list[str]:
    # A malformed result (no verdict key) must never weaken policy, so it
    # counts as a failure.
    return [r.get("verdict", "fail") for r in tool_results.values()]


def decide_status(tool_results: dict) -> Status:
    """Return the overall policy status from the per-tool results.

    - Any "fail" verdict → the trip is denied.
    - Otherwise any "needs_approval" verdict → the trip needs review (and the
      decision carries `requires_manager_approval=True`, see
      `needs_manager_approval`).
    - No tool results at all (e.g. intake was not ready, so no checks ran) →
      needs review rather than a false "approved".
    - Otherwise the trip is approved.
    """
    if not tool_results:
        return "needs_review"
    verdicts = _verdicts(tool_results)
    if "fail" in verdicts:
        return "denied"
    if "needs_approval" in verdicts:
        return "needs_review"
    return "approved"


def needs_manager_approval(tool_results: dict) -> bool:
    """True when the trip escalates rather than fails.

    At least one "needs_approval" verdict and no "fail" verdicts. A denied trip
    does not request manager approval — there is nothing left to approve.
    """
    if not tool_results:
        return False
    verdicts = _verdicts(tool_results)
    return "fail" not in verdicts and "needs_approval" in verdicts
