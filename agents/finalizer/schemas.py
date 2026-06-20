"""The finalizer's output schema is the orchestrator-level
``TravelQualificationOutput`` defined in ``agents/orchestrator/schemas.py`` and
imported by ``agent.py``. It is intentionally NOT redefined here: the finalizer
produces the *system's* final output, which is owned by the orchestrator package,
so there is a single definition rather than a duplicate that could drift.
"""

from agents.orchestrator.schemas import TravelQualificationOutput

__all__ = ["TravelQualificationOutput"]
