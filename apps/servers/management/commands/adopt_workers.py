from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.orchestration import get_orchestrator
from apps.servers.models import Server
from apps.servers.services import refresh_server
from orchestrator.errors import OrchestratorError


class Command(BaseCommand):
    help = "Register explicitly selected, already-joined Swarm workers without SSH credentials."

    def add_arguments(self, parser):
        parser.add_argument("node_ids", nargs="+")

    def handle(self, *args, **options):
        orchestrator = get_orchestrator()
        try:
            info = orchestrator.swarm_info()
            if not info or not info.control_available:
                raise CommandError("Worker adoption requires a Swarm manager.")
            nodes = {node.id: node for node in orchestrator.list_nodes()}
        except OrchestratorError as exc:
            raise CommandError(str(exc)) from exc

        adopted = []
        with transaction.atomic():
            for node_id in dict.fromkeys(options["node_ids"]):
                node = nodes.get(node_id)
                if not node or node.role != "worker" or node_id == info.node_id:
                    raise CommandError(f"{node_id!r} is not a worker in this Swarm.")
                name = (node.hostname or node.id)[:64]
                server = Server.objects.filter(swarm_node_id=node_id, is_local=False).first()
                if server is None:
                    if Server.objects.filter(name=name).exists():
                        raise CommandError(f"A different server named {name!r} is registered.")
                    server = Server.objects.create(
                        name=name, address=node.address, swarm_node_id=node_id,
                        cluster_id=info.cluster_id, role=Server.Role.WORKER,
                    )
                refresh_server(server, nodes=[node])
                adopted.append(server.name)
        for name in adopted:
            self.stdout.write(f"Registered Swarm worker: {name}")
