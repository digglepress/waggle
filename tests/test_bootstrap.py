from __future__ import annotations

from django.test import SimpleTestCase

from orchestrator.fake import FakeOrchestrator
from provisioning.bootstrap import BootstrapContext, BootstrapError, run_bootstrap
from provisioning.executor import FakeExecutor


def base_executor(
    *,
    memory: str = "Mem: 3936 512 1200\n",
    swarm_state: str = "inactive\n",
) -> FakeExecutor:
    return (
        FakeExecutor()
        .add("hostname", stdout="edge-1\n")
        .add("os-release", stdout='ID=ubuntu\nPRETTY_NAME="Ubuntu 24.04 LTS"\n')
        .add("uname -m", stdout="x86_64\n")
        .add("nproc", stdout="4\n")
        .add("free -m", stdout=memory)
        .add("df -m", stdout="Filesystem 1M-blocks Used Available Use% Mounted\n/dev/sda1 40000 9000 31000 23% /\n")
        .add("stat -fc", stdout="cgroup2fs\n")
        .add("swapon --show", stdout="")
        .add("timedatectl", stdout="yes\n")
        .add("docker version", stdout="27.5.1\n")
        .add("docker info", stdout=swarm_state)
        .add("id -u", stdout="0\n")
        .add("command -v ufw", exit_code=1)
        .add("command -v firewall-cmd", exit_code=1)
        .add("docker swarm join", stdout="This node joined a swarm as a worker.\n")
    )


class BootstrapTests(SimpleTestCase):
    def setUp(self):
        FakeOrchestrator.reset()
        self.orch = FakeOrchestrator()
        self.orch.reset_state()
        self.orch.add_node("manager-1", role="manager", address="10.0.0.1")
        self.orch.set_swarm()
        self.orch.add_node("edge-1", address="10.0.0.2")
        self.fields: dict = {}
        self.lines: list[str] = []

    def context(
        self,
        executor: FakeExecutor,
        *,
        role: str = "worker",
        node_timeout_s: float = 5,
    ) -> BootstrapContext:
        return BootstrapContext(
            executor=executor,
            orchestrator=self.orch,
            log=self.lines.append,
            data={
                "name": "edge-1",
                "address": "10.0.0.2",
                "role": role,
                "labels": {"zone": "eu"},
                "advertise_addr": "",
                "min_ram_mb": 1024,
                "node_timeout_s": node_timeout_s,
            },
            save=lambda **fields: self.fields.update(fields),
        )

    def test_full_bootstrap_reaches_ready(self):
        executor = base_executor()
        run_bootstrap(self.context(executor))

        self.assertEqual(self.fields["status"], "ready")
        self.assertEqual(self.fields["bootstrap_step"], "labels")
        self.assertEqual(self.fields["docker_version"], "27.5.1")
        self.assertEqual(self.fields["arch"], "x86_64")
        self.assertEqual(self.fields["cpu_cores"], 4)
        self.assertTrue(self.fields["swarm_node_id"].startswith("node_"))
        self.assertEqual(self.fields["cluster_id"], "fake-cluster-id")

        joined = [c for c in executor.commands if "docker swarm join" in c]
        self.assertEqual(len(joined), 1)
        self.assertIn("SWMTKN-1-fake-worker-token", joined[0])
        self.assertIn("10.0.0.1:2377", joined[0])

        update = [c for c in self.orch.calls if c[0] == "update_node"]
        self.assertEqual(len(update), 1)
        labels = update[0][2]["labels"]
        self.assertEqual(labels["zone"], "eu")
        self.assertEqual(labels["waggle.managed"], "true")
        self.assertIn("==> Joining the swarm", self.lines)

    def test_manager_role_uses_manager_token(self):
        executor = base_executor()
        run_bootstrap(self.context(executor, role="manager"))
        joined = [c for c in executor.commands if "docker swarm join" in c][0]
        self.assertIn("SWMTKN-1-fake-manager-token", joined)

    def test_preflight_failure_aborts_before_join(self):
        executor = base_executor(memory="Mem: 400 100 100\n")
        with self.assertRaises(BootstrapError) as ctx:
            run_bootstrap(self.context(executor))
        self.assertIn("preflight failed", str(ctx.exception))
        self.assertFalse([c for c in executor.commands if "docker swarm join" in c])
        self.assertNotIn("status", self.fields)

    def test_node_already_in_cluster_does_not_join_again(self):
        executor = base_executor(swarm_state="active|fake-cluster-id|node_existing|false\n")
        run_bootstrap(self.context(executor))
        self.assertFalse([c for c in executor.commands if "docker swarm join" in c])
        self.assertEqual(self.fields["status"], "ready")

    def test_node_in_another_cluster_is_rejected(self):
        executor = base_executor(swarm_state="active|some-other-cluster|node_x|false\n")
        with self.assertRaises(BootstrapError) as ctx:
            run_bootstrap(self.context(executor))
        self.assertIn("different swarm", str(ctx.exception))

    def test_missing_node_registration_times_out(self):
        self.orch.nodes.clear()
        with self.assertRaises(BootstrapError):
            run_bootstrap(self.context(base_executor(), node_timeout_s=0.05))
