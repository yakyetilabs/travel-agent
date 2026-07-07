"""The system's final output schema is defined in the finalizer package (its
producer) and re-exported here for convenience / discoverability. It is NOT
defined here to avoid a circular import: the orchestrator package imports the
finalizer agent, so the finalizer cannot import back from this package.
"""

from agents.finalizer.schemas import FinalDecision, PreTripApprovalOutput

__all__ = ["FinalDecision", "PreTripApprovalOutput"]
