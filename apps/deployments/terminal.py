import base64
import os
import re
import shlex
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import paramiko

from apps.deployments.services import owned_service
from apps.servers.models import Server
from provisioning.ssh import fingerprint, load_private_key

GUARD = Path(__file__).with_name("terminal-guard.sh")
OUTPUT_LIMIT = 64 * 1024
TRANSPORT_TIMEOUT = 32


class TerminalError(RuntimeError):
    pass


@dataclass(frozen=True)
class SSHOptions:
    host: str
    port: int
    user: str
    pin: str
    key: str = field(repr=False)
    restricted: bool = False


class Output:
    def __init__(self):
        self.buffer = bytearray()
        self.truncated = False

    def append(self, chunk):
        available = OUTPUT_LIMIT - len(self.buffer)
        self.buffer.extend(chunk[:available])
        self.truncated |= len(chunk) > available

    def result(self, exit_code):
        return {
            "output": self.buffer.decode("utf-8", "replace"),
            "truncated": self.truncated,
            "exit_code": exit_code,
        }


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, pin):
        self.pin = pin

    def missing_host_key(self, client, hostname, key):
        if fingerprint(key) != self.pin:
            raise paramiko.SSHException("SSH host key does not match the pinned fingerprint.")


def select_tasks(orchestrator, app):
    service = owned_service(orchestrator, app)
    if not service:
        return []
    return [
        task for task in orchestrator.service_tasks(service.id)
        if task.service_id == service.id and task.is_running and task.desired_state == "running"
        and re.fullmatch(r"[0-9a-f]{64}", task.container_id)
    ]


def ssh_options(node_id, cluster_id):
    server = Server.objects.filter(
        swarm_node_id=node_id, cluster_id=cluster_id, is_local=False,
    ).select_related("ssh_key", "terminalaccess").first()
    if not server:
        return None
    access = getattr(server, "terminalaccess", None)
    if access:
        return SSHOptions(server.address, access.port, "root", access.fingerprint,
                          access.private_key, restricted=True)
    if server.ssh_key and server.ssh_host_key:
        return SSHOptions(server.address, server.ssh_port, server.ssh_user,
                          server.ssh_host_key, server.ssh_key.private_key)
    return None


def run_terminal(orchestrator, app, task_id, command):
    task = next((item for item in select_tasks(orchestrator, app) if item.id == task_id), None)
    if not task:
        raise TerminalError("This replica is not running. Refresh and select another replica.")
    swarm = orchestrator.swarm_info()
    if not swarm or not swarm.control_available:
        raise TerminalError("Terminal access requires a Swarm manager.")
    args = [task.node_id, task.service_id, task.id, task.container_id, command]
    if task.node_id == swarm.node_id:
        return _run_local(args)
    ssh = ssh_options(task.node_id, swarm.cluster_id)
    if not ssh:
        raise TerminalError("This worker has no SSH credentials with a pinned host key.")
    return _run_ssh(ssh, args)


def _run_local(args):
    output = Output()
    unix = os.name == "posix"
    with subprocess.Popen(
        ["sh", str(GUARD), *args], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=unix,
    ) as proc:
        def drain():
            while chunk := proc.stdout.read(8192):
                output.append(chunk)

        reader = threading.Thread(target=drain, daemon=True)
        reader.start()
        try:
            exit_code = proc.wait(timeout=TRANSPORT_TIMEOUT)
        except subprocess.TimeoutExpired:
            if unix:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait()
            raise TerminalError("Command transport timed out.") from None
        finally:
            reader.join()
    return output.result(exit_code)


def _run_ssh(options, args):
    if options.restricted:
        encoded = base64.b64encode(args[-1].encode()).decode()
        command = shlex.join(["waggle-terminal", *args[:-1], encoded])
    else:
        sudo = [] if options.user == "root" else ["sudo", "-n"]
        command = shlex.join([*sudo, "sh", "-c", GUARD.read_text(), "waggle-terminal", *args])

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHostKey(options.pin))
    deadline = time.monotonic() + TRANSPORT_TIMEOUT
    timer = threading.Timer(TRANSPORT_TIMEOUT, client.close)
    timer.daemon = True
    timer.start()
    output = Output()
    try:
        client.connect(
            options.host, port=options.port, username=options.user,
            pkey=load_private_key(options.key), allow_agent=False, look_for_keys=False,
            timeout=5, auth_timeout=5, banner_timeout=5,
        )
        with client.get_transport().open_session(timeout=5) as channel:
            channel.set_combine_stderr(True)
            channel.exec_command(command)
            channel.shutdown_write()
            while time.monotonic() < deadline:
                if channel.recv_ready():
                    output.append(channel.recv(8192))
                elif channel.exit_status_ready():
                    return output.result(channel.recv_exit_status())
                else:
                    time.sleep(0.05)
            raise TerminalError("Command transport timed out.")
    except (EOFError, OSError, paramiko.SSHException):
        raise TerminalError("Worker SSH failed. Verify its connectivity, key and pinned fingerprint.") from None
    finally:
        timer.cancel()
        client.close()
