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
| Targets a 1 GB server | SQLite (WAL) instead of Postgres, Huey instead of Celery/Redis, hand-written CSS instead of a Node build |
| Multi-node from day one | Docker Swarm, not a single Docker host |
| One control plane | Every orchestration call goes through the manager's Docker API; SSH is only used to provision a machine |
| Swappable scheduler | `orchestrator/` is Django-free and behind an ABC |
| No build step for the UI | No npm, no CDN, works offline |

## Status

- [x] **M0 — foundations**: project layout, settings, SQLite, Huey, encrypted
      SSH keys, orchestrator abstraction with a fake backend.
- [x] **M1 — cluster**: SSH bootstrap (preflight → Docker install → firewall →
      swarm join → labels), server registry reconciled against the swarm,
      drain/activate/remove, live bootstrap logs over SSE.
- [x] **M2 — deploy**: applications, Dockerfile builds with buildx, Traefik
      routing, an opt-in registry, environment variables and Swarm secret mounts.
- [x] **M3 — day-2 ops**: runtime logs, one-step rollback, health checks,
      resource limits, Docker-host exec and control-plane database backups.
- [x] **M4 — production readiness**: pinned and signed release images, an
      upgrade path with health-gated rollback, deployment convergence with
      automatic rollback, fail-closed production settings, audit logging,
      scheduled encrypted backups, and readiness/metrics endpoints.

Waggle supports one manager. Release installs use a prebuilt, digest-pinned,
cosign-signed image; the source installer still builds locally for
development. See [SECURITY.md](SECURITY.md) before exposing it.

## Quick start

### Try the three-node lab (one command)

This local lab runs three Linux containers, each with its own Docker daemon:
one Swarm manager capped at **1 GiB** with no extra swap, and two Swarm
workers. Waggle’s web and queue processes run on the manager. The manager is
drained so Swarm schedules application workloads on the workers instead.

From this checkout with Docker running in Linux-container mode:

```bash
bash deploy/lab.sh
```

On Windows with Docker Desktop and Git for Windows, run from PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy/lab.ps1
```

Open <http://127.0.0.1:8088/login/> as `admin`. To retrieve the generated
password locally:

```bash
docker exec waggle-lab-manager cat /var/lib/waggle-lab/admin-password
```

The installer registers both workers in the **Servers** UI without SSH.
Re-run it to restart or update the lab without resetting its data or password.
Set `WAGGLE_LAB_PORT` before creating the lab to use a different host port.
Stop it without erasing data:

```bash
docker stop waggle-lab-worker-1 waggle-lab-worker-2 waggle-lab-manager
```

This is a **local development lab**, not a production topology or a security
boundary for untrusted code. The nested Docker containers run privileged.
The UI binds to localhost only, and Docker APIs and Swarm ports are not
published on the outer host. The normal installer below runs directly on a
Linux manager instead.

### Install on a Linux manager (one command)

On a fresh Ubuntu/Debian host (the idle control plane targets a 1 GB host;
allow separate headroom for builds and applications):

```bash
sudo bash deploy/install.sh
```

For a production install, use a prebuilt release image pinned by digest
instead of building on the manager:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/waggle/v1.2.3/deploy/install.sh \
  | sudo bash -s -- \
      --source https://github.com/OWNER/waggle.git --ref v1.2.3 \
      --image ghcr.io/owner/waggle@sha256:…
```

The installer clones the tagged source for the Compose file and helper
scripts, then runs the prebuilt image instead of building on the manager. From
an existing checkout, `sudo ./deploy/install.sh --image …` does the same.

Release images are signed with cosign. Verify the signature before installing,
using the identity printed in the release notes:

```bash
cosign verify \
  --certificate-identity https://github.com/OWNER/waggle/.github/workflows/release.yml@refs/tags/v1.2.3 \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  ghcr.io/owner/waggle@sha256:…
```

Set `WAGGLE_REQUIRE_SIGNATURE=1` and `WAGGLE_COSIGN_IDENTITY` to make the
installer and upgrader refuse an unsigned image. The installer rejects
mutable tags by default; `WAGGLE_ALLOW_UNPINNED=1` exists only for local tests.

For an air-gapped manager, pull the digest on a connected host, verify it,
then save and transfer the image:

```bash
docker pull ghcr.io/owner/waggle@sha256:…
docker save -o waggle-image.tar ghcr.io/owner/waggle@sha256:…
sha256sum waggle-image.tar > waggle-image.tar.sha256
# transfer both files, then:
sudo ./deploy/install.sh --image ghcr.io/owner/waggle@sha256:… \
  --offline ./waggle-image.tar
```

The installer verifies `waggle-image.tar.sha256` before loading it.

To install from source without cloning first (trusted release tag only):

```bash
curl -fsSL "$WAGGLE_INSTALL_URL" | sudo bash -s -- \
  --source "$WAGGLE_REPO_URL" --ref "$WAGGLE_REF"
```

There is no public Waggle release URL configured in this checkout. Review the
script before running it as root; prefer a signed digest over a branch.

The installer:

1. installs Docker Engine if it is missing,
2. runs `docker swarm init` on this host,
3. creates the attachable `paas_proxy` overlay network,
4. generates `deploy/.env` with a random `WAGGLE_SECRET_KEY` and secret key ring,
5. pulls the pinned signed image, or builds from the checkout when no image is
   given, and starts the control plane with Compose,
6. creates an admin user (prompted in a terminal, generated on a piped install,
   or supplied as `WAGGLE_ADMIN_PASSWORD`),
7. registers this host as the local Swarm node.

Then open <http://127.0.0.1:8000/> on the manager. For initial access from a
different machine, use an SSH tunnel, such as
`ssh -L 8000:127.0.0.1:8000 user@manager`. Save the generated login password
securely; rerunning a noninteractive install does not reset an existing admin.

### Upgrade a release

`deploy/upgrade.sh` takes a pre-upgrade database backup, verifies the image
signature when configured, switches `WAGGLE_IMAGE` in `deploy/.env`, recreates
the web and worker containers, and waits for `/readyz`:

```bash
sudo ./deploy/upgrade.sh --image ghcr.io/owner/waggle@sha256:…
```

If the new version does not become healthy within `WAGGLE_HEALTH_TIMEOUT`
(default 180 seconds), it restores the previous image and brings it back. The
database schema is upgraded by the entrypoint migration before the health
check; a rollback across an incompatible schema still needs the pre-upgrade
backup. Keep `WAGGLE_BACKUP_DIR` on a different disk or host.

### Terminate TLS in front of the control plane

Waggle trusts `X-Forwarded-Proto` from a loopback proxy. The reference setup
uses Caddy on the manager:

1. Bind Waggle to loopback: set `WAGGLE_HTTP_PORT=127.0.0.1:8000` in
   `deploy/.env`.
2. Set `WAGGLE_ALLOWED_HOSTS=your.host`,
   `WAGGLE_CSRF_TRUSTED_ORIGINS=https://your.host` and
   `WAGGLE_SECURE_COOKIES=1`. Leave `WAGGLE_SSL_REDIRECT=0`; Caddy performs
   the redirect, and redirecting twice can loop.
3. Point DNS at the manager and allow inbound TCP 80 and 443.
4. Install Caddy and use `deploy/Caddyfile.example`:
   `sudo cp deploy/Caddyfile.example /etc/caddy/Caddyfile`, replace the
   hostname, then `sudo systemctl reload caddy`.
5. Recreate the control plane:
   `docker compose --env-file deploy/.env -f deploy/docker-compose.yml up -d`.

Caddy terminates TLS and proxies to `127.0.0.1:8000`, which is never exposed
off the host. If you route applications through Traefik on the same host, give
the two proxies different public ports or terminate both at Caddy. Do not
publish port 8000 directly.

### Creating and deploying a service

Sign in as a staff user and open **Projects → New project**. Create an
environment such as **Production** or **Staging**, then choose **New service**.
Service names such as `api` and `web` can be reused in different environments.
Existing apps are imported into **Default → Production** without renaming
their Swarm services or losing their deployment history.

- **Docker image**: connect a registry first (e.g. `docker.io`, with no
  credentials for public images). Enter an image reference on that registry.
  For a demo, use `traefik/whoami:latest` and container port `80`.
- **Git repository**: connect an HTTPS Git provider, optionally with a
  BuildKit-compatible read-only access token. Enter a repository on that host,
  a full commit SHA, and use a root `Dockerfile`. An optional **Build target**
  selects a named stage. BuildKit clones HTTPS Git submodules recursively;
  select additional Git connections for private submodules on other hosts.
  Git connections use tokens,
  not OAuth. A build registry must still be configured as described below.

Source credentials are encrypted at rest. They are used during deployment,
not stored in deployment snapshots. **Environment variables** accepts either
individual form rows or `KEY=value` text; both edit the same runtime values
for this service. Values are literal; text mode supports quotes and `\n` in
double-quoted multiline values. Use Swarm secrets instead of putting
credentials in environment variables.

Save the service and click **Deploy**. The Huey worker builds (or pulls) the
image, updates the Swarm service, then waits for the requested number of tasks
to reach `running`. The deployment page shows **Waiting for tasks** during the
rollout and ends in **Deployed** only when it converges. A timeout or a failed
task marks the deployment **Failed**; an update to an existing service is
rolled back to the previous Swarm spec automatically. The application page
shows running replicas and the deployment page shows build output and errors.
The service panel can fetch recent runtime logs and queue a rollback to the
previous Swarm spec. Rolling back changes the running service, not the saved
application form. **Deployment history** also rolls back to a specific
submitted revision using its saved image and configuration without
rebuilding it. Prefer digests: mutable image tags can point to different
content on a later rollback.

Deployment and runtime logs stream with timestamps. Operators can filter
services by tag from Projects, see running replicas and task status, and
review submissions and failures in Notifications. Owners manage access
under **Team**; operators can change services and servers, while viewers
can inspect status. This is one trusted workspace, not tenant isolation.

Service settings include health-check command, interval, timeout, start
period and retries, CPU and memory limits, and named Docker volume mounts.
Volume data stays on the node with Docker’s local driver; Swarm does not
replicate that data if a task moves. Back up volumes separately.

- An image can be a tag or a digest. A Dockerfile build instead takes a public
  HTTPS Git URL ending in `.git`, a full commit SHA, and a root `Dockerfile`.
  Buildx pushes a deployment-specific tag, and Swarm uses its immutable digest.
- Runtime variables and deployment snapshots are encrypted in SQLite.
  Values are still readable by the container and anyone with Docker access.
  Create sensitive values with `docker secret create` on the manager, then list
  the secret names in the app form. They appear as files in `/run/secrets/`.
- An application publishes no host port. To reach it by hostname, configure
  the edge stack below. On the service’s **Domains** page, enter one hostname
  per line, save, then deploy to publish those routes. All assigned hostnames
  use the service’s configured container port. Each hostname belongs to one
  service; to move it, remove it from the old service and redeploy there first.
  A rollback can restore a hostname only if it is still assigned to that
  service. Only deploy trusted images and repositories: apps share the proxy
  network, and builds run on the manager.

#### Route applications with Traefik

On the manager, run `docker stack deploy -c deploy/edge.yml waggle-edge`, point
the app’s DNS hostname at the manager, and deploy the app with that hostname.
The stack runs Traefik in global mode on **manager nodes only** so its local
Docker socket can read Swarm state. It publishes host ports 80/443.
For automatic Let’s Encrypt certificates, point public DNS at the single
manager and allow inbound TCP 80 and 443. Then deploy the ACME override:

```bash
export WAGGLE_ACME_EMAIL=operator@example.org
docker stack deploy -c deploy/edge.yml -c deploy/edge-acme.yml waggle-edge
```

Set `WAGGLE_ROUTE_TLS=1` and `WAGGLE_TRAEFIK_CERT_RESOLVER=letsencrypt` in
`deploy/.env`, recreate the worker, and redeploy the routed services. Traefik
keeps ACME state and private keys in the `traefik-acme` volume. The local lab
does not qualify for public certificates. For a staging test, override
`WAGGLE_ACME_CA_SERVER` and use a separate stack/volume.

Alternatively, provide a certificate covering your app hostnames:

```bash
docker secret create waggle_edge_certificate /path/to/fullchain.pem
docker secret create waggle_edge_key /path/to/privkey.pem
docker stack deploy -c deploy/edge.yml -c deploy/edge-tls.yml waggle-edge
```

Set `WAGGLE_ROUTE_TLS=1` in `deploy/.env`, recreate the worker, and redeploy
the applications to replace their HTTP routing labels with HTTPS labels.

#### Configure a build registry and private image credentials

Use an existing registry or start the bundled TLS- and password-protected
`registry:2`. The manager and all workers must resolve the registry hostname,
trust its TLS certificate and be allowed through the firewall. Limit access
to the cluster’s private network.

1. For the bundled registry, prepare `tls.crt` (with the registry hostname),
   `tls.key`, and a bcrypt `htpasswd` file in a private directory **outside**
   the checkout. Set `WAGGLE_REGISTRY_FILES` to that absolute path and
   `WAGGLE_REGISTRY_BIND` to the manager's private IP and port
   (e.g. `10.0.0.10:5000`). Start it with
   `docker compose --env-file deploy/.env -f deploy/registry.yml up -d`.
2. Set `WAGGLE_REGISTRY_HOST` in `deploy/.env` to the reachable authority
   (e.g. `registry.example.org:5000`, without `https://`). Add any private CA
   to the Docker Engine registry trust store on the manager and every worker.
3. For authentication, create a private Docker configuration directory outside
   the checkout and run `docker --config /path/to/config login registry.example.org:5000`.
   Use a configuration with inline `auths`, not a host-only credential helper.
   Set `WAGGLE_DOCKER_CONFIG_DIR` to that directory in `deploy/.env` and start:

   ```bash
   docker compose --env-file deploy/.env -f deploy/docker-compose.yml \
     -f deploy/docker-auth.yml up -d --build
   ```

   The worker uses the mounted Docker credentials for Buildx pushes. The Python
   Docker SDK reads the same credentials and forwards registry authentication
   to Swarm when creating or updating the service. Use a dedicated registry
   credential and protect the Docker config file.

### Open an application shell

Open a service’s **Terminal** page to select a running container and run a
command from the browser. The console shows output and exit codes, limits
commands to 20 seconds and output to 64 KiB, and does not store commands or
output in Waggle. Commands do not share an interactive session. The target
image needs `/bin/sh` and `timeout`.

The lab enables this for both workers over private, host-key-pinned SSH
without publishing worker SSH ports. In an existing lab, redeploy services
once so their recreated containers carry Waggle’s terminal access label.
Set `WAGGLE_LAB_TERMINAL=0` before running the lab installer to disable the
browser console. Run the installer again after restarting the lab workers
to start their restricted SSH listeners.

For non-lab installs, the browser console is off by default. Set
`WAGGLE_WEB_TERMINAL=1` only for a trusted, TLS-protected installation. Local
tasks use the manager socket. Remote tasks need an SSH-provisioned node
with a pinned host fingerprint and Docker access (root or passwordless
sudo); adopted nodes without credentials cannot be selected for execution
until configured. Viewers cannot run commands.

For a persistent interactive shell, use the operator CLI:

From the manager, use the same Waggle installation and Docker context that
created the service. The command defaults to `/bin/sh` and needs a shell in the
image. It opens a TTY when run in an interactive terminal.

```bash
python manage.py exec_app my-app
python manage.py exec_app --task TASK_ID my-app -- ls -l /app
python manage.py exec_app --locate my-app
python manage.py exec_app --task TASK_ID --files /app my-app
python manage.py exec_app --task TASK_ID --read /app/settings.txt my-app
```

For a Compose installation, use
`docker compose -f deploy/docker-compose.yml exec web python manage.py exec_app my-app`.
The command can exec only into tasks on the node targeted by the local Docker
CLI. `--locate` prints the task, node and container IDs; if the task runs on a
worker, pass an authorized `--context WORKER_CONTEXT` or connect to its host
and run `docker exec -it CONTAINER_ID /bin/sh` there. The service’s
**Containers** page lists task locations. Open **Files** from a service or
its Containers page for a read-only directory browser and UTF-8 previews up
to 24 KiB. Browsing uses the same operator permissions, node checks and
opt-in setting as Terminal. The container image needs `sh`, `timeout`,
`find`, `head`, and `base64`; binary previews and editing are not supported.

### Automate deployments with the API

Create a scoped token on the manager. The raw token is printed once:

```bash
docker compose -f deploy/docker-compose.yml exec -T web \
  python manage.py create_api_token --name ci --scope read --scope deploy
```

Send it as a bearer token over TLS:

```bash
curl -fsS -H "Authorization: Bearer $WAGGLE_TOKEN" \
  https://waggle.example.org/api/v1/services/

curl -fsS -X POST -H "Authorization: Bearer $WAGGLE_TOKEN" \
  https://waggle.example.org/api/v1/services/12/deploy

curl -fsS -H "Authorization: Bearer $WAGGLE_TOKEN" \
  https://waggle.example.org/api/v1/deployments/34/
```

`read` lists services and deployment status; `deploy` also queues deploys and
rollbacks. `POST /services/<id>/rollback` accepts an optional JSON body
`{"deployment_id": 34}` to return to a specific successful revision. A second
deploy while one is active returns `409`. List tokens with
`create_api_token --list`, and disable one with `--revoke PREFIX`. Tokens are
stored as SHA-256 digests; losing the raw value means issuing a new token.

The API is intentionally small. There is no endpoint for editing
configuration or reading secrets, environment values or logs.

### Back up and restore the control plane

`backup_data` takes a consistent SQLite snapshot, including changes in WAL,
without stopping the web process. Choose a new file in a private directory:

```bash
python manage.py backup_data /path/to/private/waggle-backup.sqlite3
python manage.py restore_data /path/to/private/waggle-backup.sqlite3 \
  --data-dir /path/to/new-waggle-data
```

For Compose, run `backup_data` in `web` with a new filename under
`/var/lib/waggle` and copy the result out with `docker compose cp`. The
convenience wrapper does that, compresses the result, writes a SHA-256 file,
and prunes old copies:

```bash
sudo ./deploy/backup.sh
```

Set `WAGGLE_BACKUP_DIR` (default `/var/backups/waggle`),
`WAGGLE_BACKUP_KEEP_DAYS` (default 14) and `WAGGLE_BACKUP_AGE_RECIPIENT` to
encrypt with `age` before the file leaves the host. To schedule it:

```bash
sudo install -m 0644 deploy/waggle-backup.service deploy/waggle-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now waggle-backup.timer
```

A backup is only useful if it restores. Run the drill in
`tests/test_backups.py`, or restore into a new data directory and start a
second control plane against it before deleting the old one. To recover,
run `restore_data` with the backup mounted read-only and an empty volume
mounted at the target directory. Stop the original web and worker before
switching them to the recovered volume. Restore never overwrites existing
database or queue files.

Keep the original `WAGGLE_SECRET_KEY` separately and use it when starting
from the recovered database, or stored SSH keys cannot be decrypted. The
backup does **not** include Docker volumes, images, Swarm state, registry data,
logs or the Huey queue. Restore marks in-flight jobs as interrupted; inspect
Swarm before retrying them, especially if the cluster kept running.

### Back up and restore an application volume

Service data lives in Swarm-local volumes, which are not replicated and are not
part of the control-plane backup. Back up and restore on the node that holds
the volume:

```bash
sudo ./deploy/volume-backup.sh backup \
  waggle-service-12-api-data /var/backups/waggle/volumes

sudo ./deploy/volume-backup.sh restore \
  waggle-service-12-api-data \
  /var/backups/waggle/volumes/waggle-service-12-api-data-20261124T020000Z.tgz --yes
```

The backup is a compressed tar written with a SHA-256 file; restore verifies
the checksum and the archive before writing, and requires `--yes`. Volume
names are shown on the service page. Stop or scale the service to zero before a
backup if the application needs a consistent snapshot. Restore does not delete
files that are absent from the archive. Copy the archive to another host after
the backup; a volume on the same node is not a disaster-recovery copy.

### Monitor the control plane

The `/healthz` endpoint is a lightweight database liveness check. `/readyz`
performs the same check for a load balancer and returns `503` when the
database is unavailable; both include the running version. Compose uses
`/readyz` for the container health check. Set `WAGGLE_LOG_JSON=1` to emit one
JSON object per line for Loki, CloudWatch or ELK. Security-relevant operator
actions (deployments, rollbacks, service and variable edits, team changes,
terminal commands, server removal) are recorded in **Audit log** under
`/audit/`.

To enable `/metrics`, set `WAGGLE_METRICS_TOKEN` to a random secret and recreate the web
container. Set `WAGGLE_METRICS_TOKEN` only on trusted hosts, keep the scrape on
TLS or a private network, and store the same token in Prometheus using
`authorization.credentials_file`:

```yaml
scrape_configs:
  - job_name: waggle
    scheme: https
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/waggle-token
    static_configs:
      - targets: [waggle.example.org]
```

Metrics contain aggregate counts of saved applications, registered servers by
last saved state, and deployment records by state. They do not call Docker or
measure live container CPU/memory. The endpoint is disabled without the token.
`deploy/prometheus-alerts.yml` is a starting rule set for outage, failed or
stuck deployments, and non-ready servers.

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

The test suite covers spec conversion, the fake orchestrator, Docker SDK
integration via mocks, preflight parsers, provisioning, log tailing, encrypted
fields, and the HTTP layer. It does not require a Docker daemon or network.

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
