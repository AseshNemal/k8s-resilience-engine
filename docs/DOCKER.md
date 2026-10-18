# Running the Resilience Engine in Docker Desktop

The dashboard and API run in a container next to a PostgreSQL container. The Kubernetes test
cluster is a separate local [kind](https://kind.sigs.k8s.io/) cluster (1 control-plane + 3 workers)
that the app can read from. Creating the cluster is a separate step on purpose: starting the app
never creates, changes, or disrupts anything in Kubernetes.

```
Docker Desktop
├─ docker compose (project: resilience-engine)
│    ├─ app        FastAPI + dashboard   → http://localhost:8000 (loopback only)
│    └─ postgres   named volume "pgdata", not published to the host
└─ kind cluster "resilience-research"     created by scripts/cluster-up.sh
        ▲ app joins the "kind" Docker network and reaches https://resilience-research-control-plane:6443
```

## Prerequisites

- Docker Desktop with Docker Compose v2. 4 GB of memory allocated to Docker Desktop was enough for
  the cluster, the app and PostgreSQL together (verified on macOS, Apple silicon).
- [`kind`](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) and `kubectl` on your `PATH`
  (macOS: `brew install kind kubectl`).
- A bash shell. Verified on macOS only; Linux should work, Windows needs WSL2 (untested).

## First-time setup

```bash
git clone <repo-url> && cd k8s-resilience-engine

cp .env.example .env            # then edit POSTGRES_PASSWORD (letters, digits, - _ . ~ only)
./scripts/cluster-up.sh         # creates the kind cluster if missing, writes .kube/kind-internal.config
docker compose up --build -d    # builds the image, starts PostgreSQL, runs migrations, starts the app
```

Open **http://localhost:8000** (API docs: `/docs`, health: `/api/health`).

`cluster-up.sh` is safe to re-run: it keeps an existing cluster and its workloads, restarts stopped
node containers, and only reports success once the Kubernetes API answers. The first run downloads
the node image and takes a few minutes.

## Day to day

```bash
docker compose up -d            # start (keeps all data)
docker compose ps               # status and health
docker compose logs -f app      # follow application logs
docker compose restart app      # e.g. after re-running ./scripts/cluster-up.sh
docker compose down             # stop and remove the containers (data is kept)
```

- `docker compose down` keeps the database. **`docker compose down -v` deletes it.**
- The kind cluster keeps running after `docker compose down`. It stops with Docker Desktop; re-run
  `./scripts/cluster-up.sh` after Docker Desktop restarts and it will start the nodes again.
- Deleting the cluster is always a manual decision: `kind delete cluster --name resilience-research`
  (this removes every workload in it).
- Database shell: `docker compose exec postgres psql -U resilience -d resilience_engine`
  (use your `POSTGRES_USER` / `POSTGRES_DB` if you changed them).
- Running the tests (the app image has no test tooling; this uses a throwaway container):

  ```bash
  docker run --rm --entrypoint sh -v "$PWD/tests:/app/tests:ro" -e DATABASE_URL=sqlite:////tmp/test.db \
    resilience-engine-app -c 'grep -iE "^(pytest|iniconfig|pluggy|pygments)==" requirements.txt | pip install -q -r /dev/stdin && python -m pytest -q -p no:cacheprovider'
  ```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `cluster-up.sh`: cannot reach the Docker daemon | Start Docker Desktop, wait until it says running, retry. |
| `cluster-up.sh`: API server not reachable | Run the `docker ps` / `docker logs` commands it prints. The script never deletes a cluster. |
| `compose up`: `POSTGRES_PASSWORD` is required | `cp .env.example .env` and set a password. |
| `compose up`: bind source path does not exist | `.kube/kind-internal.config` is missing. Run `./scripts/cluster-up.sh`. |
| `compose up`: network `kind` not found | Same: the `kind` network is created together with the cluster. |
| Live topology is empty or errors after Docker Desktop restarted | Re-run `./scripts/cluster-up.sh`, then `docker compose restart app`. |
| Port 8000 already in use | Stop the other process (for example a local `uvicorn`) or change the host port in `compose.yaml`. |
| `password authentication failed` after editing `.env` | PostgreSQL keeps the password it was first created with. Change it inside the database, or reset with `docker compose down -v` (deletes the data). |

## Optional: Jaeger

Jaeger is not part of this stack; the app only contacts it when you use the workflow component's live
trace features. To use one, start it separately and allow its hostname:

```bash
docker compose -f deploy/docker-compose.jaeger.yml up -d
# in .env:  WORKFLOW_JAEGER_ALLOWED_HOSTS=localhost,127.0.0.1,::1,jaeger,host.docker.internal
docker compose up -d                       # picks up the new variable
```

Then enter `http://host.docker.internal:16686` as the Jaeger URL in the dashboard. The dashboard
pre-fills `http://localhost:16686`, which points at the app container itself when it runs in Docker.
Only the container-to-Jaeger network path was checked; the workflow features were not exercised.
Pointing the workflow probes at an Online Boutique frontend needs `WORKFLOW_TARGET_ALLOWED_HOSTS` /
`WORKFLOW_BOUTIQUE_ALLOWED_HOSTS` in the same way (not set up here).

## Security notes

- `.kube/kind-internal.config` holds **cluster-admin credentials** for the local test cluster.
  It is git-ignored and written with mode 600. Mounting it read-only into the container prevents
  edits to the file, **not** use of the credentials: the app is not restricted to read-only
  Kubernetes access, because no restricted RBAC role is configured. Do not share or commit it.
- The container starts as root only to copy the kubeconfig into a private in-memory directory owned
  by UID 10001, then drops to UID 10001 with no Linux capabilities. Your personal `~/.kube/config`
  and the Docker socket are never mounted.
- The app has no authentication, so port 8000 is published on `127.0.0.1` only. PostgreSQL is not
  published to the host.
- The code in this repository only reads from the cluster and never applies recommendations.
  Startup runs database migrations and the web server and nothing else.

## Current limitations

- Only Component 2 (deployment topology, `/api/deployments/live`) reads the live cluster.
  Components 3 and 4 still serve sample data, there is no recommendation apply or validation step,
  and Components 1 to 4 are not yet connected end to end.
- The fresh kind cluster contains no application workloads, so live topology shows the three
  worker nodes and no services. Online Boutique is not deployed into kind by this setup.
- kind worker nodes are containers on the same Docker host. They are **logical** worker-node failure
  domains, not independent physical machines, and share one kernel and one Docker VM. Results
  from this setup are not evidence about independent hardware failures.
- Component 1's pages default to `localhost` URLs; see the Jaeger section above.
- The app image does not hot-reload code. Rebuild with `docker compose up --build -d` after changes.
