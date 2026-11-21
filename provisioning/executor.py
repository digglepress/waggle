"""Remote command execution, abstracted so provisioning can be tested.

Every provisioning step takes an :class:`Executor`. In production that is an
SSH connection; in tests it is :class:`FakeExecutor`. Nothing else in the
provisioning package knows the difference.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    @property
    def combined(self) -> str:
        return (self.stdout + ("\n" if self.stdout and self.stderr else "") + self.stderr).strip()

    def tail(self, lines: int = 20) -> str:
        text = self.combined
        return "\n".join(text.splitlines()[-lines:])


class RemoteCommandError(RuntimeError):
    def __init__(self, result: CommandResult) -> None:
        self.result = result
        super().__init__(
            f"command failed with exit code {result.exit_code}: {result.command}\n"
            f"{result.tail()}"
        )


class Executor(Protocol):
    """Runs shell commands on a target host."""

    def run(
        self,
        command: str,
        *,
        check: bool = True,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult: ...

    def close(self) -> None: ...


@dataclass
class FakeExecutor:
    """A scripted executor: match a substring of the command, return a result."""

    responses: list[tuple[str, CommandResult]] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    default_exit_code: int = 0
    closed: bool = False

    def add(
        self,
        pattern: str,
        stdout: str = "",
        stderr: str = "",
        exit_code: int = 0,
    ) -> FakeExecutor:
        self.responses.append(
            (pattern, CommandResult(command=pattern, exit_code=exit_code, stdout=stdout, stderr=stderr))
        )
        return self

    def run(
        self,
        command: str,
        *,
        check: bool = True,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        self.commands.append(command)
        result = self._match(command)
        if on_line:
            for line in (result.stdout + result.stderr).splitlines():
                on_line(line)
        if check and not result.ok:
            raise RemoteCommandError(result)
        return result

    def _match(self, command: str) -> CommandResult:
        # Later additions win, so a test can override a default response.
        for pattern, result in reversed(self.responses):
            if pattern in command:
                return CommandResult(
                    command=command,
                    exit_code=result.exit_code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                )
        return CommandResult(command=command, exit_code=self.default_exit_code)

    def close(self) -> None:
        self.closed = True

    def saw(self, pattern: str) -> bool:
        return any(pattern in command for command in self.commands)

    def __enter__(self) -> FakeExecutor:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
