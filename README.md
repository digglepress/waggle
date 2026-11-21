# Waggle

A lightweight, Swarm-native platform-as-a-service. Coolify's workflow, Docker
Swarm's scheduler, one small Django process.

Waggle gives you a web UI and a control plane for running containers across a
set of servers you own. You point it at a machine, it installs Docker, joins the
machine to a Swarm, and from then on you deploy applications to the cluster
instead of to a box.

```
                    ┌──────────────────────────────┐
   browser ────────▶│  Waggle (Django, SQLite)     │
                    │  web + Huey worker           │
                    └──────────────┬───────────────┘
                                   │ Docker API (/var/run/docker.sock)
                    ┌──────────────▼───────────────┐
                    │        Swarm manager         │
                    │   scheduler + routing mesh   │
                    └───────┬──────────────┬───────┘
                            │              │
                    ┌───────▼──────┐ ┌─────▼────────┐
                    │   worker 1   │ │   worker 2   │
                    └──────────────┘ └──────────────┘
```

## Why another PaaS

Coolify is excellent, but it needs Redis, Postgres, a queue, and a Node build
step before it can schedule anything. That is a lot of moving parts for a
two-server deployment.

Waggle's constraints:

| Constraint | Consequence |
| --- | --- |
| Fits a 1 GB server | SQLite (WAL) instead of Postgres, Huey instead of Celery/Redis, hand-written CSS instead of a Node build |
| Multi-node from day one | Docker Swarm, not a single Docker host |
| One control plane | Every orchestration call goes through the manager's Docker API; SSH is only used to provision a machine |
| Swappable scheduler | `orchestrator/` is Django-free and behind an ABC |
| No build step for the UI | No npm, no CDN, works offline |

## Status

Milestone 1 of 4 — the cluster layer — is implemented and tested.

- [x] **M0 — foundations**: project layout, settings, SQLite, Huey, encrypted
      SSH keys, orchestrator abstraction with a fake backend.
- [x] **M1 — cluster**: SSH bootstrap (preflight → Docker install → firewall →
      swarm join → labels), server registry reconciled against the swarm,
      drain/activate/remove, live bootstrap logs over SSE.
- [ ] **M2 — deploy**: applications, Dockerfile builds with buildx, Traefik
      routing, bundled registry, environment variables and secrets.
- [ ] **M3 — day-2 ops**: logs, exec, rollbacks, health, resource limits, backups.
- [ ] **M4 — polish**: onboarding, metrics, multi-manager, docs.

Applications, builds and Traefik are deliberately absent: M1 manages *machines*,
and says so in the UI. The installer serves plain HTTP on port 8000; see
[SECURITY.md](SECURITY.md) before exposing it.

Known gaps carried into M2:

- `docker-py` 7.2 has no `registry_auth` argument on service create/update, so
  the `--with-registry-auth` equivalent for private images needs a raw API call
  or the CLI.
- One manager. Multi-manager failover is an M4 item.
- No TLS yet; `WAGGLE_SECURE_COOKIES=0` is the M1 default.

## Quick start

On a fresh Ubuntu/Debian host (1 vCPU / 1 GB is enough for the control plane):

```bash
git clone https://github.com/<you>/waggle.git
cd waggle
sudo ./deploy/install.sh
```

The installer:

1. installs Docker Engine if it is missing,
2. runs `docker swarm init` on this host,
3. creates the attachable `paas_proxy` overlay network,
4. generates `deploy/.env` with a random `WAGGLE_SECRET_KEY`,
5. builds and starts the control plane with Compose,
6. creates an admin user (prompted, or from `WAGGLE_ADMIN_PASSWORD`),
7. registers this host as the local Swarm node.

Then open `http://<host>:8000/` and sign in.

### Adding a worker

1. Make sure the worker is reachable over SSH and has passwordless `sudo`.
2. Put your public key in its `authorized_keys`.
3. In Waggle: **SSH keys → Add key** (paste the *private* key; it is encrypted
   at rest with `SECRET_KEY`).
4. **Servers → Add server**: address, SSH user, role (`worker`, or `manager`
   for a second manager).

Waggle connects, pins the host key (trust on first use), checks architecture,
memory, disk, cgroups and clock sync, installs Docker with a hardened
`daemon.json`, opens the Swarm ports, joins the node with a freshly fetched
token, and applies labels. Progress streams into the server page.

## How it works

### Orchestration

All scheduler access goes through `orchestrator/base.py`:

```python
class Orchestrator(ABC):
    def ping(self) -> bool: ...
    def list_nodes(self) -> list[NodeInfo]: ...
    def create_service(self, spec: ServiceSpec) -> str: ...
    def update_service(self, name_or_id: str, spec: ServiceSpec) -> None: ...
    ...
```

`ServiceSpec` is the desired state; `orchestrator/convert.py` turns it into the
Docker API payload (pure functions, unit-tested). `orchestrator/fake.py` is an
in-memory backend used by every test, so the suite never needs a Docker daemon.
`orchestrator/swarm.py` is the real implementation.

### Provisioning

`provisioning/` is also Django-free. It speaks to a machine through an
`Executor` protocol (`provisioning/executor.py`): `SSHExecutor` in production,
`FakeExecutor` in tests. `provisioning/bootstrap.py` is a six-step, idempotent
state machine:

| Step | What it does |
| --- | --- |
| `connect` | Open SSH, pin the host key, check sudo |
| `preflight` | OS, arch, memory, disk, cgroup v2, swap, NTP, Docker, existing swarm |
| `docker` | Install Docker Engine, write `daemon.json`, start the service |
| `firewall` | Open 2377/tcp, 7946/tcp+udp, 4789/udp in ufw/firewalld if present |
| `swarm` | Fetch a join token from the manager and `docker swarm join` |
| `labels` | Wait for the node to register, apply labels, mark it ready |

Every step is safe to re-run; a failed bootstrap resumes from the beginning
without duplicating the join.

### Live logs

The worker writes plain lines to `data/logs/server-<id>.log`. The server page
tails that file over server-sent events (`EventSource`), so there is no Redis,
no Channels, and the stream survives a web-process restart.

## Development

Requirements: Python 3.12+ (3.13 recommended), Docker only if you want to run
the real orchestrator.

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
source .venv/bin/activate         # macOS / Linux
pip install -r requirements-dev.txt

python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Run the Huey worker in a second terminal (bootstrap runs there):

```bash
python -m huey.bin.huey_consumer config.worker.huey -w 2 -k thread
# or: huey_consumer config.worker.huey -w 2 -k thread
```

For UI work without a worker, set `WAGGLE_HUEY_IMMEDIATE=1` so tasks run inline.

### Tests

```bash
python manage.py test tests -v 2
ruff check .
```

The suite (80 tests) covers spec conversion, the fake orchestrator, preflight
parsers, the full bootstrap state machine, Docker installation, log tailing,
encrypted fields and the HTTP layer. No Docker daemon, no network.

### Configuration

Everything is environment-driven (`config/settings/base.py`):

| Variable | Default | Purpose |
| --- | --- | --- |
| `WAGGLE_SECRET_KEY` | insecure dev key | Django secret; also derives the SSH-key encryption key |
| `WAGGLE_DEBUG` | `0` | Django debug mode |
| `WAGGLE_ALLOWED_HOSTS` | empty (required in prod) | Comma-separated hosts |
| `WAGGLE_DATA_DIR` | `./data` | SQLite database, logs, Huey queue |
| `WAGGLE_ORCHESTRATOR` | `orchestrator.swarm:SwarmOrchestrator` | Backend import path |
| `WAGGLE_DOCKER_URL` | unset → `docker.from_env()` | Remote Docker endpoint |
| `WAGGLE_PROXY_NETWORK` | `paas_proxy` | Overlay shared with the edge proxy |
| `WAGGLE_SECURE_COOKIES` | `1` in prod settings | Set `0` while serving plain HTTP |
| `WAGGLE_SSH_CONNECT_TIMEOUT` | `15` | Seconds |
| `WAGGLE_SSH_COMMAND_TIMEOUT` | `900` | Seconds |
| `WAGGLE_NODE_MIN_RAM_MB` | `1024` | Preflight minimum |
| `WAGGLE_HUEY_IMMEDIATE` | `0` | Run tasks inline (development) |

`WAGGLE_SECRET_KEY` encrypts stored SSH private keys. Changing it makes existing
keys unreadable; re-add them if you rotate it.

## Security

Waggle mounts `/var/run/docker.sock`, which is equivalent to root on the manager.
It is a single-tenant control plane for infrastructure you own, not a shared
hosting platform. See [SECURITY.md](SECURITY.md) for the threat model and the
steps to take before exposing it.

## License

Apache-2.0. See [LICENSE](LICENSE).
