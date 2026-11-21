from __future__ import annotations

import json

from django.test import SimpleTestCase

from provisioning.docker_install import daemon_config, ensure_docker
from provisioning.executor import FakeExecutor


def quiet(_line: str) -> None:
    pass


class DaemonConfigTests(SimpleTestCase):
    def test_each_call_returns_an_independent_dict(self):
        first = daemon_config()
        first["log-opts"]["max-size"] = "1g"
        self.assertEqual(daemon_config()["log-opts"]["max-size"], "10m")

    def test_mtu_is_optional(self):
        self.assertNotIn("mtu", daemon_config())
        self.assertEqual(daemon_config(mtu=1450)["mtu"], 1450)


class EnsureDockerTests(SimpleTestCase):
    def test_matching_config_is_left_alone(self):
        ex = (
            FakeExecutor()
            .add("docker version", stdout="27.5.1\n")
            .add("cat /etc/docker/daemon.json", stdout=json.dumps(daemon_config()))
        )
        result = ensure_docker(ex, quiet)

        self.assertEqual(result.version, "27.5.1")
        self.assertFalse(result.installed_now)
        self.assertFalse(result.daemon_config_changed)
        self.assertFalse(ex.saw("base64 -d"))
        self.assertFalse(ex.saw("systemctl"))

    def test_changed_config_is_written_and_docker_restarts(self):
        ex = (
            FakeExecutor()
            .add("docker version", stdout="27.5.1\n")
            .add("cat /etc/docker/daemon.json", exit_code=1)
        )
        result = ensure_docker(ex, quiet)

        self.assertTrue(result.daemon_config_changed)
        self.assertTrue(ex.saw("base64 -d > /etc/docker/daemon.json"))
        # ``enable --now`` would not restart a running daemon, so the command
        # must ask for a restart explicitly.
        self.assertTrue(ex.saw("systemctl restart docker"))
