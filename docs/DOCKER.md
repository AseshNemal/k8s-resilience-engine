# Running the Resilience Engine in Docker Desktop

The dashboard and API run in one container with SQLite stored on a Docker named volume. The
Kubernetes test cluster is a separate local [kind](https://kind.sigs.k8s.io/) cluster
(1 control-plane + 3 workers) that the app can read from. Creating the cluster is a separate step on
purpose: starting the app never creates, changes, or disrupts anything in Kubernetes.

```
Docker Desktop
├─ docker compose (project: resilience-engine)
│    └─ app   FastAPI + dashboard → http://localhost:8000 (loopback only)
│             SQLite on named volume "appdata" (/data)
└─ kind cluster "resilience-research"   created by scripts/cluster-up.sh
        ▲ the app reaches the API server through the port kind publishes on the host
          (https://host.docker.internal:<port>, certificate verified)
```

## Prerequisites

- **Docker Desktop** with Docker Compose v2. The app reaches the cluster through
  `host.docker.internal`, which Docker Desktop provides. Plain Docker Engine on Linux is not supported.
  4 GB of memory allocated to Docker Desktop was enough for the cluster and the app (verified on macOS,
  Apple silicon).
- [`kind`](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) and `kubectl` on your `PATH`
  (macOS: `brew install kind kubectl`).
- A bash shell. Verified on macOS only; Windows needs WSL2 (untested).

## First-time setup

```bash
git clone <repo-url> && cd k8s-resilience-engine

./scripts/cluster-up.sh         # creates the kind cluster if missing, writes .kube/kind-app.config
docker compose up --build -d    # builds the image, creates the database, starts the app
```

Open **http://localhost:8000** (API docs: `/docs`, health: `/api/health`). No `.env` file is needed.

`cluster-up.sh` is safe to re-run: it keeps an existing cluster and its workloads, restarts stopped
node containers, and only reports success once the Kubernetes API answers. The first run downloads
the node image and takes a few minutes. The database is created and migrated automatically on every
start (`alembic upgrade head`; it does nothing when already up to date).

## Day to day

```bash
docker compose up -d            # start (keeps all data)
docker compose ps               # status and health
docker compose logs -f app      # follow application logs
docker compose restart app      # e.g. after re-running ./scripts/cluster-up.sh
docker compose down             # stop and remove the container (data is kept)
```

- Your data lives in the `resilience-engine_appdata` volume. `docker compose down` and rebuilds keep it.
  **`docker compose down -v` deletes it.**
- Look inside the database:
  `docker compose exec app python -c "import sqlite3; c=sqlite3.connect('file:/data/resilience.db?mode=ro', uri=True); print(c.execute('select name from sqlite_master').fetchall())"`
- The kind cluster keeps running after `docker compose down`. It stops with Docker Desktop; re-run
  `./scripts/cluster-up.sh` after Docker Desktop restarts (it starts the nodes again), then
  `docker compose restart app`.
- Deleting the cluster is always a manual decision: `kind delete cluster --name resilience-research`
  (this removes every workload in it).
- Running the tests (the app image has no test tooling; this uses a throwaway container):

  ```bash
  docker run --rm --entrypoint sh -v "$PWD/tests:/app/tests:ro" -e DATABASE_URL=sqlite:////tmp/test.db \
    resilience-engine-app -c 'grep -iE "^(pytest|iniconfig|pluggy|pygments)==" requirements.txt | pip install -q -r /dev/stdin && python -m pytest -q -p no:cacheprovider'
  ```

### Using PostgreSQL instead

SQLite is the default. The application still supports PostgreSQL: replace the `DATABASE_URL` line
in `compose.yaml` with a `postgresql://` URL for a server you run yourself (the driver is installed in
the image). Compose no longer starts a PostgreSQL container.

If you used an earlier version of this setup that ran PostgreSQL in Compose, its data is still in the
`resilience-engine_pgdata` volume. Nothing reads or deletes it. Remove it yourself with
`docker volume rm resilience-engine_pgdata` once you no longer need it.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cluster-up.sh`: cannot reach the Docker daemon | Start Docker Desktop, wait until it says running, retry. |
| `cluster-up.sh`: API server not reachable | Run the `docker ps` / `docker logs` commands it prints. The script never deletes a cluster. |
| `compose up`: bind source path does not exist | `.kube/kind-app.config` is missing. Run `./scripts/cluster-up.sh`. |
| Live topology errors after Docker Desktop restarted, or after the cluster was recreated | Re-run `./scripts/cluster-up.sh`, then `docker compose restart app` (the published API port can change). |
| Port 8000 already in use | Stop the other process (for example a local `uvicorn`) or change the host port in `compose.yaml`. |

## Optional: Jaeger

Jaeger is not part of this stack; the app only contacts it when you use the workflow component's live
trace features (without it those endpoints return a 503 saying Jaeger is unreachable). To use one,
start it separately and allow its hostname:

```bash
docker compose -f deploy/docker-compose.jaeger.yml up -d
# create .env with:  WORKFLOW_JAEGER_ALLOWED_HOSTS=localhost,127.0.0.1,::1,jaeger,host.docker.internal
docker compose up -d                       # picks up the new variable
```

Then enter `http://host.docker.internal:16686` as the Jaeger URL in the dashboard. The dashboard
pre-fills `http://localhost:16686`, which points at the app container itself when it runs in Docker.
Only the container-to-Jaeger network path was checked; the workflow features were not exercised.
Pointing the workflow probes at an Online Boutique frontend needs `WORKFLOW_TARGET_ALLOWED_HOSTS` /
`WORKFLOW_BOUTIQUE_ALLOWED_HOSTS` in the same way (not set up here).

## Security notes

- **The app has no authentication, and workloads in the kind cluster can still reach it.** This was
  tested on Docker Desktop for Mac (engine 29.8.2). Pods were not tested directly; they leave the
  cluster through the node containers, which were:

  | From a kind node container | Result |
  |---|---|
  | `http://host.docker.internal:8000` (the published port) | reachable |
  | the app container's IP on its Docker network | reachable |
  | container name or service name | not resolvable |

  The app is deliberately not attached to the `kind` network, so the cluster cannot find it by name.
  But Docker Desktop routes between Docker networks and gives every path the same source address,
  so neither network settings nor binding the listener to an interface can keep cluster workloads
  out. Closing it needs either authentication or no host-published TCP port, for example serving the
  UI through a Docker Desktop Extension socket. Neither is part of this setup.
- What a caller gets: the same read-only views and approval-record writes the dashboard has. The
  repository contains no Kubernetes write calls, and no API route returns the kubeconfig. Treat the
  test cluster as one that should only run workloads you trust.
- `.kube/kind-app.config` holds **cluster-admin credentials** for the local test cluster. It is
  git-ignored and written with mode 600. Mounting it read-only prevents edits to the file, **not** use
  of the credentials: no restricted RBAC role is configured. Do not share or commit it.
- The container starts as root only to prepare `/data` and copy the kubeconfig into a private in-memory
  directory owned by UID 10001, then drops to UID 10001 with no Linux capabilities. Your personal
  `~/.kube/config` and the Docker socket are never mounted.
- Port 8000 is published on `127.0.0.1` only.
- Startup runs database migrations and the web server and nothing else. The code only reads from the
  cluster and never applies recommendations.

## Current limitations

- Component 2 (deployment topology, `/api/deployments/discovery` and `/live`) reads the live cluster.
  Component 3 predicts over that live topology plus an approved, frozen workflow, and refuses when either
  is missing or incomplete. Component 4 has no live recommendation source yet, so no approval can be
  requested. There is no recommendation apply or validation step. No endpoint serves sample data.
- The fresh kind cluster contains no application workloads, so live topology shows the three
  worker nodes and an empty (valid) result with no services. Online Boutique is not deployed into kind by
  this setup.
- Service/ownership resolution follows the Service selector and owner references, so applications whose
  Deployment names differ from their pods' `app` label (Istio Bookinfo) resolve completely; this is covered
  by tests on a fake cluster, not yet by a real Bookinfo run. The live recommendation pipeline is incomplete
  (`docs/RECOMMENDATION_LIVE_DATA.md`).
- kind worker nodes are containers on the same Docker host. They are **logical** worker-node failure
  domains, not independent physical machines, and share one kernel and one Docker VM. Results
  from this setup are not evidence about independent hardware failures.
- Component 1's pages default to `localhost` URLs; see the Jaeger section above.
- The app image does not hot-reload code. Rebuild with `docker compose up --build -d` after changes.
