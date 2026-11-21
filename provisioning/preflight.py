"""Preflight checks run before a server is allowed to join the swarm.

Every check is a pure parse of a command's output, so the parsers are unit
tested without SSH and the checks themselves are testable with ``FakeExecutor``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .executor import Executor

SUPPORTED_OS = {
    "ubuntu",
    "debian",
    "raspbian",
    "linuxmint",
    "centos",
    "rhel",
    "rocky",
    "almalinux",
    "fedora",
    "amzn",
    "opensuse-leap",
    "opensuse-tumbleweed",
}

SUPPORTED_ARCH = {"x86_64", "amd64", "aarch64", "arm64"}

SWARM_PORTS = ("2377/tcp", "7946/tcp", "7946/udp", "4789/udp")


@dataclass(frozen=True, slots=True)
class Check:
    id: str
    label: str
    status: str  # ok | warn | fail | skip
    detail: str = ""

    @property
    def is_failure(self) -> bool:
        return self.status == "fail"


@dataclass(frozen=True, slots=True)
class Report:
    checks: tuple[Check, ...]
    facts: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(check.is_failure for check in self.checks)

    @property
    def failures(self) -> list[Check]:
        return [check for check in self.checks if check.is_failure]

    @property
    def warnings(self) -> list[Check]:
        return [check for check in self.checks if check.status == "warn"]


# --------------------------------------------------------------------------- #
# Parsers (pure)
# --------------------------------------------------------------------------- #


def parse_os_release(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_mem_total_mb(text: str) -> int:
    for line in text.splitlines():
        if line.lower().startswith("mem:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    return int(parts[1])
                except ValueError:
                    return 0
    return 0


def parse_df_available_mb(text: str) -> int:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return 0
    parts = lines[1].split()
    if len(parts) < 4:
        return 0
    try:
        return int(parts[3])
    except ValueError:
        return 0


def parse_int(text: str) -> int:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return 0


def parse_cgroup_version(text: str) -> str:
    value = text.strip()
    if value == "cgroup2fs":
        return "v2"
    if value == "tmpfs":
        return "v1"
    return value or "unknown"


def parse_swarm_state(text: str) -> str:
    value = text.strip()
    return value if value and value != "<no value>" else "inactive"


def parse_ntp_synced(text: str) -> bool | None:
    value = text.strip().lower()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #


def _run(ex: Executor, command: str, *, timeout: int = 60) -> str:
    result = ex.run(command, check=False, timeout=timeout)
    return result.stdout if result.ok else ""


def run_preflight(
    ex: Executor,
    *,
    min_ram_mb: int = 1024,
    min_disk_gb: int = 10,
) -> Report:
    facts: dict[str, Any] = {}
    checks: list[Check] = []

    os_release = parse_os_release(_run(ex, "cat /etc/os-release"))
    facts["os_id"] = os_release.get("ID", "")
    facts["os_name"] = os_release.get("PRETTY_NAME") or os_release.get("NAME", "")
    facts["os_version"] = os_release.get("VERSION_ID", "")

    arch = _run(ex, "uname -m").strip()
    facts["arch"] = arch

    cpus = parse_int(_run(ex, "nproc"))
    facts["cpu_cores"] = cpus

    ram_mb = parse_mem_total_mb(_run(ex, "free -m"))
    facts["ram_mb"] = ram_mb

    disk_mb = parse_df_available_mb(_run(ex, "df -m /var/lib || df -m /"))
    facts["disk_gb"] = disk_mb // 1024

    cgroup = parse_cgroup_version(_run(ex, "stat -fc %T /sys/fs/cgroup"))
    facts["cgroup"] = cgroup

    swap = _run(ex, "swapon --show")
    facts["swap_enabled"] = bool(swap.strip())

    ntp = parse_ntp_synced(_run(ex, "timedatectl show -p NTPSynchronized --value"))
    facts["ntp_synced"] = ntp

    docker_version = _run(ex, "docker version --format '{{.Server.Version}}'", timeout=30).strip()
    facts["docker_version"] = "" if docker_version == "<no value>" else docker_version

    swarm_state = parse_swarm_state(
        _run(ex, "docker info --format '{{.Swarm.LocalNodeState}}'", timeout=30)
    )
    facts["swarm_state"] = swarm_state

    uid = parse_int(_run(ex, "id -u"))
    sudo_ok = uid == 0 or ex.run("sudo -n true", check=False, timeout=30).ok
    facts["is_root"] = uid == 0
    facts["passwordless_sudo"] = sudo_ok

    # ---- verdicts ------------------------------------------------------- #

    if arch in SUPPORTED_ARCH:
        checks.append(Check("arch", "CPU architecture", "ok", arch))
    elif arch:
        checks.append(Check("arch", "CPU architecture", "fail", f"unsupported: {arch}"))
    else:
        checks.append(Check("arch", "CPU architecture", "fail", "could not detect"))

    os_id = facts["os_id"]
    if os_id in SUPPORTED_OS:
        checks.append(Check("os", "Operating system", "ok", facts["os_name"] or os_id))
    elif os_id:
        checks.append(
            Check(
                "os",
                "Operating system",
                "warn",
                f"{facts['os_name'] or os_id} is untested; Ubuntu/Debian recommended",
            )
        )
    else:
        checks.append(Check("os", "Operating system", "warn", "could not read /etc/os-release"))

    if cgroup == "v2":
        checks.append(Check("cgroup", "cgroup version", "ok", "v2"))
    elif cgroup == "v1":
        checks.append(Check("cgroup", "cgroup version", "warn", "cgroup v1 (v2 recommended)"))
    else:
        checks.append(Check("cgroup", "cgroup version", "skip", cgroup))

    if ram_mb >= min_ram_mb:
        checks.append(Check("memory", "Memory", "ok", f"{ram_mb} MB"))
    elif ram_mb >= 512:
        checks.append(
            Check("memory", "Memory", "warn", f"{ram_mb} MB (below {min_ram_mb} MB recommended)")
        )
    else:
        checks.append(Check("memory", "Memory", "fail", f"{ram_mb} MB is too little"))

    disk_gb = facts["disk_gb"]
    if disk_gb >= min_disk_gb:
        checks.append(Check("disk", "Free disk", "ok", f"{disk_gb} GB on /var/lib"))
    elif disk_gb >= 5:
        checks.append(
            Check("disk", "Free disk", "warn", f"{disk_gb} GB free (below {min_disk_gb} GB)")
        )
    else:
        checks.append(Check("disk", "Free disk", "fail", f"{disk_gb} GB free is too little"))

    if sudo_ok:
        checks.append(Check("sudo", "Privileges", "ok", "root" if uid == 0 else "passwordless sudo"))
    else:
        checks.append(
            Check(
                "sudo",
                "Privileges",
                "fail",
                "passwordless sudo is required for the SSH user",
            )
        )

    if not facts["swap_enabled"]:
        checks.append(Check("swap", "Swap", "ok", "disabled"))
    else:
        checks.append(Check("swap", "Swap", "warn", "swap is enabled; Swarm prefers it off"))

    if ntp is True:
        checks.append(Check("clock", "Clock sync", "ok", "NTP synchronised"))
    elif ntp is False:
        checks.append(Check("clock", "Clock sync", "warn", "NTP not synchronised; Swarm is clock-sensitive"))
    else:
        checks.append(Check("clock", "Clock sync", "skip", "timedatectl unavailable"))

    if facts["docker_version"]:
        checks.append(Check("docker", "Docker Engine", "ok", facts["docker_version"]))
    else:
        checks.append(Check("docker", "Docker Engine", "skip", "not installed yet"))

    if swarm_state == "inactive":
        checks.append(Check("swarm", "Swarm membership", "ok", "not in a swarm yet"))
    elif swarm_state == "active":
        checks.append(Check("swarm", "Swarm membership", "ok", "already active"))
    else:
        checks.append(Check("swarm", "Swarm membership", "warn", swarm_state))

    return Report(checks=tuple(checks), facts=facts)


def describe_ports() -> str:
    return ", ".join(SWARM_PORTS)
