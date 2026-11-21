from __future__ import annotations

from django.test import SimpleTestCase

from provisioning.executor import FakeExecutor
from provisioning.preflight import (
    parse_cgroup_version,
    parse_df_available_mb,
    parse_mem_total_mb,
    parse_ntp_synced,
    parse_os_release,
    run_preflight,
)


def happy_executor() -> FakeExecutor:
    return (
        FakeExecutor()
        .add(
            "os-release",
            stdout='NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nPRETTY_NAME="Ubuntu 24.04.1 LTS"\n',
        )
        .add("uname -m", stdout="x86_64\n")
        .add("nproc", stdout="4\n")
        .add(
            "free -m",
            stdout="               total        used        free\nMem:            3936         512        1200\n",
        )
        .add(
            "df -m",
            stdout="Filesystem     1M-blocks  Used Available Use% Mounted on\n/dev/sda1         40000  9000     31000  23% /\n",
        )
        .add("stat -fc", stdout="cgroup2fs\n")
        .add("swapon --show", stdout="")
        .add("timedatectl", stdout="yes\n")
        .add("docker version", stdout="27.5.1\n")
        .add("docker info", stdout="inactive\n")
        .add("id -u", stdout="0\n")
    )


class ParserTests(SimpleTestCase):
    def test_os_release(self):
        values = parse_os_release('NAME="Debian"\nID=debian\nVERSION_ID="12"\n')
        self.assertEqual(values["ID"], "debian")
        self.assertEqual(values["VERSION_ID"], "12")

    def test_memory(self):
        self.assertEqual(parse_mem_total_mb("Mem:  1979  300  500\nSwap: 0 0 0\n"), 1979)
        self.assertEqual(parse_mem_total_mb("garbage"), 0)

    def test_disk(self):
        text = "Filesystem 1M-blocks Used Available Use% Mounted\n/dev/sda1 40000 9000 31000 23% /\n"
        self.assertEqual(parse_df_available_mb(text), 31000)

    def test_cgroup(self):
        self.assertEqual(parse_cgroup_version("cgroup2fs"), "v2")
        self.assertEqual(parse_cgroup_version("tmpfs"), "v1")
        self.assertEqual(parse_cgroup_version(""), "unknown")

    def test_ntp(self):
        self.assertTrue(parse_ntp_synced("yes"))
        self.assertFalse(parse_ntp_synced("no"))
        self.assertIsNone(parse_ntp_synced(""))


class PreflightTests(SimpleTestCase):
    def test_happy_path(self):
        report = run_preflight(happy_executor())
        self.assertTrue(report.ok, [c for c in report.checks if c.is_failure])
        self.assertEqual(report.facts["arch"], "x86_64")
        self.assertEqual(report.facts["ram_mb"], 3936)
        self.assertEqual(report.facts["disk_gb"], 30)
        self.assertEqual(report.facts["cgroup"], "v2")
        self.assertEqual(report.facts["docker_version"], "27.5.1")

    def test_low_memory_fails(self):
        ex = happy_executor().add("free -m", stdout="Mem: 400 100 100\n")
        report = run_preflight(ex)
        self.assertFalse(report.ok)
        self.assertIn("Memory", [check.label for check in report.failures])

    def test_no_sudo_fails(self):
        ex = (
            FakeExecutor()
            .add("id -u", stdout="1000\n")
            .add("sudo -n true", exit_code=1)
        )
        report = run_preflight(ex)
        labels = [check.label for check in report.failures]
        self.assertIn("Privileges", labels)

    def test_unsupported_arch_fails(self):
        report = run_preflight(happy_executor().add("uname -m", stdout="i686\n"))
        self.assertFalse(report.ok)

    def test_swap_is_a_warning_not_a_failure(self):
        report = run_preflight(happy_executor().add("swapon --show", stdout="/dev/sda2\n"))
        self.assertTrue(report.ok)
        self.assertIn("Swap", [check.label for check in report.warnings])

    def test_missing_docker_is_skipped_not_failed(self):
        report = run_preflight(happy_executor().add("docker version", stdout="", exit_code=127))
        self.assertTrue(report.ok)
        docker = next(check for check in report.checks if check.id == "docker")
        self.assertEqual(docker.status, "skip")
