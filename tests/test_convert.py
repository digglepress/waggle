"""The spec -> Docker payload conversion is where subtle bugs live.

These tests pin the things that are easy to get wrong: nanosecond durations,
Resources/Placement living on the TaskTemplate, and service-vs-container
labels (Traefik reads the former).
"""

from __future__ import annotations

from django.test import SimpleTestCase

from orchestrator.convert import (
    create_kwargs,
    endpoint_spec,
    service_mode,
    task_template,
    update_config,
    update_kwargs,
)
from orchestrator.spec import (
    FileRef,
    Healthcheck,
    Mount,
    Placement,
    PortMapping,
    Resources,
    ServiceSpec,
    UpdateConfig,
)


class TaskTemplateTests(SimpleTestCase):
    def test_container_spec_carries_env_command_and_user(self):
        spec = ServiceSpec(
            name="app",
            image="ghcr.io/acme/app:1",
            env={"A": "1", "B": "two"},
            command=["/bin/sh", "-c"],
            args=["echo hi"],
            user="1000:1000",
            working_dir="/srv",
            init=True,
        )
        container = task_template(spec)["ContainerSpec"]
        self.assertEqual(container["Env"], ["A=1", "B=two"])
        self.assertEqual(container["Command"], ["/bin/sh", "-c"])
        self.assertEqual(container["Args"], ["echo hi"])
        self.assertEqual(container["User"], "1000:1000")
        self.assertEqual(container["Dir"], "/srv")
        self.assertTrue(container["Init"])

    def test_resources_and_placement_are_siblings_of_container_spec(self):
        spec = ServiceSpec(
            name="app",
            image="app:1",
            resources=Resources(cpus=0.5, memory_mb=256, reserve_memory_mb=64),
            placement=Placement(constraints=("node.labels.zone == eu",)),
        )
        template = task_template(spec)
        self.assertNotIn("Resources", template["ContainerSpec"])
        self.assertNotIn("Placement", template["ContainerSpec"])
        self.assertEqual(template["Resources"]["Limits"]["NanoCPUs"], 500_000_000)
        self.assertEqual(template["Resources"]["Limits"]["MemoryBytes"], 256 * 1024 * 1024)
        self.assertEqual(template["Resources"]["Reservations"]["MemoryBytes"], 64 * 1024 * 1024)
        self.assertEqual(template["Placement"]["Constraints"], ["node.labels.zone == eu"])

    def test_healthcheck_uses_shell_and_nanoseconds(self):
        spec = ServiceSpec(
            name="app",
            image="app:1",
            healthcheck=Healthcheck(
                command="curl -fsS localhost:8000/healthz",
                interval_s=10,
                timeout_s=2,
                start_period_s=30,
                retries=5,
            ),
        )
        check = task_template(spec)["ContainerSpec"]["Healthcheck"]
        self.assertEqual(check["Test"], ["CMD-SHELL", "curl -fsS localhost:8000/healthz"])
        self.assertEqual(check["Interval"], 10_000_000_000)
        self.assertEqual(check["Timeout"], 2_000_000_000)
        self.assertEqual(check["StartPeriod"], 30_000_000_000)
        self.assertEqual(check["Retries"], 5)

    def test_mounts_and_secrets(self):
        spec = ServiceSpec(
            name="db",
            image="postgres:17",
            mounts=(Mount(target="/var/lib/postgresql/data", source="pg_data"),),
            secrets=(FileRef(name="pg_password", target="password", mode=0o400),),
        )
        container = task_template(spec)["ContainerSpec"]
        self.assertEqual(
            container["Mounts"],
            [
                {
                    "Type": "volume",
                    "Target": "/var/lib/postgresql/data",
                    "Source": "pg_data",
                    "ReadOnly": False,
                }
            ],
        )
        secret = container["Secrets"][0]
        self.assertEqual(secret["SecretName"], "pg_password")
        self.assertEqual(secret["File"]["Name"], "password")
        self.assertEqual(secret["File"]["Mode"], 0o400)

    def test_networks_are_ids_on_the_task_template(self):
        spec = ServiceSpec(name="app", image="app:1", networks=("net_a", "net_b"))
        template = task_template(spec)
        self.assertEqual(template["Networks"], [{"Target": "net_a"}, {"Target": "net_b"}])
        self.assertNotIn("Networks", template["ContainerSpec"])

    def test_log_rotation_is_always_set(self):
        driver = task_template(ServiceSpec(name="app", image="app:1"))["LogDriver"]
        self.assertEqual(driver["Name"], "json-file")
        self.assertEqual(driver["Options"]["max-size"], "10m")


class ServicePayloadTests(SimpleTestCase):
    def test_service_labels_are_not_container_labels(self):
        spec = ServiceSpec(
            name="app",
            image="app:1",
            labels={"traefik.enable": "true"},
            container_labels={"com.example.role": "web"},
        )
        payload = create_kwargs(spec)
        self.assertEqual(payload["labels"], {"traefik.enable": "true"})
        self.assertEqual(
            payload["task_template"]["ContainerSpec"]["Labels"],
            {"com.example.role": "web"},
        )

    def test_replicated_and_global_modes(self):
        self.assertEqual(
            service_mode(ServiceSpec(name="a", image="a", replicas=3)),
            {"Replicated": {"Replicas": 3}},
        )
        self.assertEqual(
            service_mode(ServiceSpec(name="a", image="a", global_mode=True)),
            {"Global": {}},
        )

    def test_update_config_defaults_are_zero_downtime(self):
        config = update_config(ServiceSpec(name="a", image="a"))
        self.assertEqual(config["Order"], "start-first")
        self.assertEqual(config["FailureAction"], "rollback")
        self.assertEqual(config["Delay"], 5_000_000_000)
        self.assertEqual(config["Monitor"], 30_000_000_000)

    def test_custom_update_config(self):
        spec = ServiceSpec(
            name="a",
            image="a",
            update_config=UpdateConfig(
                parallelism=2,
                order="stop-first",
                failure_action="pause",
                delay_s=0,
                monitor_s=10,
            ),
        )
        config = update_config(spec)
        self.assertEqual(config["Parallelism"], 2)
        self.assertEqual(config["Order"], "stop-first")
        self.assertEqual(config["FailureAction"], "pause")
        self.assertEqual(config["Delay"], 0)

    def test_endpoint_spec_only_when_ports_are_published(self):
        self.assertIsNone(endpoint_spec(ServiceSpec(name="a", image="a")))
        spec = ServiceSpec(
            name="proxy",
            image="traefik",
            ports=(
                PortMapping(target=80, published=80, mode="host"),
                PortMapping(target=443, published=443, mode="host"),
            ),
        )
        endpoint = endpoint_spec(spec)
        self.assertEqual(endpoint["Mode"], "vip")
        self.assertEqual(endpoint["Ports"][0]["PublishMode"], "host")
        self.assertEqual(endpoint["Ports"][1]["PublishedPort"], 443)

    def test_update_payload_has_no_name(self):
        payload = update_kwargs(ServiceSpec(name="a", image="a"))
        self.assertNotIn("name", payload)
        self.assertIn("task_template", payload)
