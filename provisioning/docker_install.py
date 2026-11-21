"""Install Docker Engine on a fresh node and write a sane daemon.json.

Idempotent: running this twice is a no-op if Docker is already present and the
daemon configuration already matches.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass

from .executor import Executor, RemoteCommandError

DEFAULT_DAEMON_CONFIG: dict[str, object] = {
    "log-driver": "json-file",
    "log-opts": {"max-size": "10m", "max-file": "3"},
    "live-restore": True,
    "features": {"buildkit": True},
}

INSTALL_SCRIPT_URL = "https://get.docker.com"


@dataclass(frozen=True, slots=True)
class DockerInstallResult:
    version: str
    installed_now: bool
    daemon_config_changed: bool


def daemon_config(*, mtu: int | None = None) -> dict[str, object]:
    config = json.loads(json.dumps(DEFAULT_DAEMON_CONFIG))
    if mtu:
        config["mtu"] = int(mtu)
    return config


def server_version(ex: Executor) -> str:
    result = ex.run("docker version --format '{{.Server.Version}}'", check=False, timeout=30)
    if not result.ok:
        return ""
    version = result.stdout.strip()
    return "" if version in {"", "<no value>"} else version


def ensure_docker(
    ex: Executor,
    log: Callable[[str], None],
    *,
    mtu: int | None = None,
) -> DockerInstallResult:
    version = server_version(ex)
    installed_now = False

    if not version:
        log("Docker Engine not running; installing from get.docker.com")
        _ensure_command(ex, log, "curl")
        ex.run(
            f"curl -fsSL {INSTALL_SCRIPT_URL} -o /tmp/get-docker.sh && sh /tmp/get-docker.sh",
            timeout=900,
            on_line=log,
        )
        installed_now = True
        version = server_version(ex)
        if not version:
            raise RemoteCommandError(
                ex.run("docker version", check=False, timeout=30)
            )

    changed = _write_daemon_config(ex, daemon_config(mtu=mtu), log)
    if changed or installed_now:
        _restart_docker(ex, log)
        version = server_version(ex)
        if not version:
            raise RemoteCommandError(ex.run("docker info", check=False, timeout=30))

    log(f"Docker Engine {version} is running")
    return DockerInstallResult(
        version=version,
        installed_now=installed_now,
        daemon_config_changed=changed,
    )


def _write_daemon_config(
    ex: Executor, config: dict[str, object], log: Callable[[str], None]
) -> bool:
    current = ex.run("cat /etc/docker/daemon.json 2>/dev/null", check=False).stdout
    if _same_json(current, config):
        return False

    encoded = base64.b64encode(json.dumps(config, indent=2).encode()).decode()
    ex.run(
        "mkdir -p /etc/docker && "
        f"echo {encoded} | base64 -d > /etc/docker/daemon.json && "
        "chmod 0644 /etc/docker/daemon.json"
    )
    log("wrote /etc/docker/daemon.json")
    return True


def _same_json(raw: str, expected: dict[str, object]) -> bool:
    if not raw.strip():
        return False
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return False
    return parsed == expected


def _restart_docker(ex: Executor, log: Callable[[str], None]) -> None:
    log("restarting Docker")
    result = ex.run("systemctl enable --now docker", check=False, timeout=180)
    if result.ok:
        return
    result = ex.run("service docker start", check=False, timeout=180)
    if result.ok:
        return
    # `systemctl` inside an unprivileged container cannot manage the host.
    raise RemoteCommandError(result)


def _ensure_command(ex: Executor, log: Callable[[str], None], command: str) -> None:
    if ex.run(f"command -v {command}", check=False).ok:
        return
    log(f"installing {command}")
    if ex.run("command -v apt-get", check=False).ok:
        ex.run(
            "DEBIAN_FRONTEND=noninteractive apt-get update -qq && "
            f"DEBIAN_FRONTEND=noninteractive apt-get install -y {command}",
            timeout=600,
            on_line=log,
        )
    elif ex.run("command -v dnf", check=False).ok:
        ex.run(f"dnf install -y {command}", timeout=600, on_line=log)
    elif ex.run("command -v yum", check=False).ok:
        ex.run(f"yum install -y {command}", timeout=600, on_line=log)
    else:
        raise RemoteCommandError(
            ex.run(f"{command} is required but no supported package manager was found", check=False)
        )
