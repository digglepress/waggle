"""Provisioning: everything that happens before a server joins the swarm.

This package never imports Django. Steps receive an :class:`Executor` and an
orchestrator, which makes them testable with fakes and reusable outside the web
app (for example from a CLI).
"""

from __future__ import annotations

from .executor import CommandResult, Executor, FakeExecutor, RemoteCommandError

__all__ = ["CommandResult", "Executor", "FakeExecutor", "RemoteCommandError"]
