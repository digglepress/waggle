"""Constants shared by the model, the provisioning layer and the UI."""

from __future__ import annotations

#: Mirrors ``provisioning.bootstrap.STEPS``. Kept here so migrations and
#: templates do not import the provisioning package.
STEP_IDS: tuple[str, ...] = (
    "connect",
    "preflight",
    "docker",
    "firewall",
    "swarm",
    "labels",
)

STEP_TITLES: dict[str, str] = {
    "connect": "Connect over SSH",
    "preflight": "Check the host",
    "docker": "Install Docker Engine",
    "firewall": "Open swarm ports",
    "swarm": "Join the swarm",
    "labels": "Apply node labels",
}
