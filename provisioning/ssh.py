"""SSH transport for provisioning remote nodes.

Design notes:

* We use Paramiko rather than shelling out to ``ssh`` so the control-plane
  container does not need an SSH client or key files on disk.
* Host keys are trust-on-first-use: the fingerprint is returned after connect
  and stored on the ``Server`` row. A later mismatch aborts the connection,
  which is the behaviour you want when a VPS is rebuilt under the same IP.
* Commands run under ``bash -c`` so pipes and redirections work, and under
  ``sudo -n`` when the SSH user is not root. Passwordless sudo is required;
  we never store or forward passwords.
"""

from __future__ import annotations

import base64
import hashlib
import io
import shlex
import time
from collections.abc import Callable

import paramiko

from .executor import CommandResult, RemoteCommandError


def load_private_key(material: str, password: str | None = None) -> paramiko.PKey:
    """Parse an OpenSSH/PEM private key without touching the filesystem."""
    errors: list[str] = []
    candidates = [
        getattr(paramiko, name, None)
        for name in ("Ed25519Key", "ECDSAKey", "RSAKey", "DSSKey")
    ]
    for key_class in candidates:
        if key_class is None:
            continue
        try:
            return key_class.from_private_key(io.StringIO(material), password=password)
        except (paramiko.SSHException, ValueError) as exc:  # wrong key type is expected
            errors.append(f"{key_class.__name__}: {exc}")
    raise paramiko.SSHException("could not parse private key (" + "; ".join(errors) + ")")


def fingerprint(key: paramiko.PKey) -> str:
    """SHA256 fingerprint in the same format ``ssh-keygen -lf`` prints."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class SSHExecutor:
    def __init__(
        self,
        host: str,
        *,
        port: int = 22,
        user: str = "root",
        private_key: str | None = None,
        password: str | None = None,
        connect_timeout: int = 15,
        command_timeout: int = 900,
        expected_fingerprint: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.private_key = private_key
        self.password = password
        self.connect_timeout = connect_timeout
        self.command_timeout = command_timeout
        self.expected_fingerprint = expected_fingerprint
        self.host_fingerprint = ""
        self._client: paramiko.SSHClient | None = None
        self._use_sudo = False

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def connect(self) -> None:
        if self._client is not None:
            return
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        pkey = load_private_key(self.private_key) if self.private_key else None
        try:
            client.connect(
                hostname=self.host,
                port=self.port,
                username=self.user,
                pkey=pkey,
                password=self.password,
                timeout=self.connect_timeout,
                banner_timeout=self.connect_timeout,
                auth_timeout=self.connect_timeout,
                allow_agent=pkey is None,
                look_for_keys=pkey is None,
            )
        except paramiko.AuthenticationException as exc:
            client.close()
            raise ConnectionError(f"SSH authentication failed for {self.user}@{self.host}") from exc
        except (paramiko.SSHException, OSError) as exc:
            client.close()
            raise ConnectionError(f"SSH connection to {self.host}:{self.port} failed: {exc}") from exc

        transport = client.get_transport()
        if transport is None:
            client.close()
            raise ConnectionError("SSH transport disappeared during handshake")
        self.host_fingerprint = fingerprint(transport.get_remote_server_key())
        if self.expected_fingerprint and self.expected_fingerprint != self.host_fingerprint:
            client.close()
            raise ConnectionError(
                "SSH host key changed: "
                f"expected {self.expected_fingerprint}, got {self.host_fingerprint}"
            )

        self._client = client
        self._detect_sudo()

    def _detect_sudo(self) -> None:
        result = self.run("id -u", check=False)
        self._use_sudo = result.ok and result.stdout.strip() != "0"

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> SSHExecutor:
        self.connect()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Execution
    # ------------------------------------------------------------------ #

    def run(
        self,
        command: str,
        *,
        check: bool = True,
        timeout: int | None = None,
        on_line: Callable[[str], None] | None = None,
    ) -> CommandResult:
        if self._client is None:
            self.connect()
        wrapped = self._wrap(command)
        result = self._execute(wrapped, command, timeout or self.command_timeout, on_line)
        if check and not result.ok:
            raise RemoteCommandError(result)
        return result

    def _wrap(self, command: str) -> str:
        quoted = shlex.quote(command)
        if self._use_sudo:
            return f"sudo -n bash -c {quoted}"
        return f"bash -c {quoted}"

    def _execute(
        self,
        wrapped: str,
        original: str,
        timeout: int,
        on_line: Callable[[str], None] | None,
    ) -> CommandResult:
        assert self._client is not None
        transport = self._client.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH connection is not active")

        channel = transport.open_session(timeout=timeout)
        channel.settimeout(0.2)
        channel.exec_command(wrapped)

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        pending = {"out": "", "err": ""}
        deadline = time.monotonic() + timeout

        def emit(kind: str, text: str) -> None:
            if not on_line:
                return
            pending[kind] += text
            while "\n" in pending[kind]:
                line, pending[kind] = pending[kind].split("\n", 1)
                on_line(line.rstrip("\r"))

        try:
            while True:
                if time.monotonic() > deadline:
                    channel.close()
                    raise TimeoutError(f"command timed out after {timeout}s: {original}")

                progressed = False
                while channel.recv_ready():
                    data = channel.recv(65536)
                    if not data:
                        break
                    stdout_chunks.append(data)
                    emit("out", data.decode("utf-8", "replace"))
                    progressed = True
                while channel.recv_stderr_ready():
                    data = channel.recv_stderr(65536)
                    if not data:
                        break
                    stderr_chunks.append(data)
                    emit("err", data.decode("utf-8", "replace"))
                    progressed = True

                if (
                    channel.exit_status_ready()
                    and not channel.recv_ready()
                    and not channel.recv_stderr_ready()
                ):
                    break
                if not progressed:
                    time.sleep(0.05)
            exit_code = channel.recv_exit_status()
        finally:
            channel.close()

        if on_line:
            for kind in ("out", "err"):
                if pending[kind]:
                    on_line(pending[kind].rstrip("\r"))
                    pending[kind] = ""

        return CommandResult(
            command=original,
            exit_code=exit_code,
            stdout=b"".join(stdout_chunks).decode("utf-8", "replace"),
            stderr=b"".join(stderr_chunks).decode("utf-8", "replace"),
        )
