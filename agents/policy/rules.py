"""Reference implementation of the policy decision rule.

The policy agent's prompt cites this function as the canonical decision logic so
the prompt and code can't describe different rules. `tool_results` maps each tool
name to its returned dict (which carries an `allowed` bool).
"""

from .schemas import Status


def decide_status(tool_results: dict) -> Status:
    """Return the overall policy status from the per-tool results.

    - If any tool reports `allowed=False`, the trip is denied.
    - If there are no tool results at all (e.g. intake was not ready, so no checks
      ran), the trip needs review rather than a false "approved".
    - Otherwise the trip is approved.

    Note: `requires_manager_approval` (set for business/first cabins) is a separate
    flag on PolicyDecision, not a status — an approved trip can still require
    manager sign-off.
    """
    if not tool_results:
        return "needs_review"
    if any(not r.get("allowed", False) for r in tool_results.values()):
        return "denied"
    return "approved"
