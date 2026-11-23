import re
import sys

import paramiko
from django.core.management.base import BaseCommand, CommandError

from apps.deployments.models import TerminalAccess
from apps.servers.models import Server
from provisioning.ssh import load_private_key


class Command(BaseCommand):
    help = "Configure a worker's restricted terminal SSH endpoint. Read the private key from stdin."

    def add_arguments(self, parser):
        parser.add_argument("node_id")
        parser.add_argument("fingerprint")
        parser.add_argument("--port", type=int, default=2222)

    def handle(self, *args, **options):
        pin, port = options["fingerprint"], options["port"]
        if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", pin) or not 1 <= port <= 65535:
            raise CommandError("Provide an OpenSSH SHA256 fingerprint and a valid port.")
        try:
            server = Server.objects.get(swarm_node_id=options["node_id"], is_local=False)
        except (Server.DoesNotExist, Server.MultipleObjectsReturned) as exc:
            raise CommandError("Expected exactly one registered worker with that node ID.") from exc
        key = sys.stdin.read(16_385)
        if len(key) > 16_384:
            raise CommandError("SSH key exceeds the accepted size.")
        try:
            load_private_key(key)
        except paramiko.SSHException:
            raise CommandError("Expected an unencrypted OpenSSH private key on stdin.") from None
        TerminalAccess.objects.update_or_create(
            server=server, defaults={"fingerprint": pin, "port": port, "private_key": key},
        )
        self.stdout.write(f"Terminal access configured for {server.name}.")
