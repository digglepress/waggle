"""Errors raised by orchestrator backends.

Every backend translates its native exceptions into these, so callers never
have to import ``docker.errors``.
"""

from __future__ import annotations


class OrchestratorError(RuntimeError):
    """Base class for anything that went wrong talking to the orchestrator."""


class OrchestratorUnavailable(OrchestratorError):
    """The orchestrator API could not be reached at all."""


class ServiceNotFound(OrchestratorError):
    """No service with that name or id exists."""


class ConvergenceError(OrchestratorError):
    """A service did not reach its desired state in time."""

    def __init__(self, message: str, *, result=None) -> None:
        super().__init__(message)
        self.result = result
