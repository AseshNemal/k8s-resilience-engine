# Docker Desktop extension

K8s Resilience Engine as a Docker Desktop extension: the existing dashboard opens as a tab inside
Docker Desktop and talks to its backend over a Unix socket. There is no browser tab, no
`docker compose` command, and **no TCP port**.

> Status: installed and tested in Docker Desktop 4.95.0 (macOS, Apple silicon). See
> [Verification status](#verification-status) for exactly what was and was not checked.

```
Docker Desktop
├─ Extension tab "Resilience Engine"     the existing pages, rendered to static files
│        │  window.ddClient.extension.vm.service   (fetch adapter: extension/ui-src/shim.js)
│        ▼
├─ Extension backend (VM container)      FastAPI on /run/guest-services/k8s-resilience-engine.sock
│        │                               SQLite + stored read-only credential on a named volume
│        ▼  read-only token, https://host.docker.internal:<port>
└─ kind cluster "resilience-research"    created by scripts/cluster-up.sh (separate step)
```

## What is reused and what is new

- **Reused unchanged:** the FastAPI app (`app/`), every dashboard page, template, stylesheet and
  script (`app/static` is copied byte-for-byte), the database models and Alembic migrations.
- **New, in `extension/`:** the socket entrypoint, the cluster-connection endpoints, the error
  envelope, the build-time page renderer, the `fetch` adapter and the "Connect cluster" page. `extension/asgi.py` imports
  the unmodified `app.main:app` and adds one router, so `app/main.py` is not edited.
- **How the pages run:** `extension/build_ui.py` runs the real app once at image build time, saves
  each page as static HTML and rewrites only asset and navigation paths. `shim.js` is loaded first
  and sends the pages' `fetch("/api/...")` calls through the Docker Desktop SDK to the backend.

## Build and install

```bash
docker build -f extension/Dockerfile -t k8s-resilience-engine:0.1.0 .
docker extension install k8s-resilience-engine:0.1.0
```

Docker Desktop must allow local extensions: **Settings → Extensions → untick "Allow only extensions
distributed through the Docker Marketplace"**. This is your security setting and, on managed
machines, may be locked by policy. Nothing is published to Docker Hub or the Marketplace.

Update: build a new tag and run `docker extension update k8s-resilience-engine:<new tag>`. The
container and image are replaced; **the data volume, and so your database and stored credential,
are kept.**

Remove: `docker extension uninstall k8s-resilience-engine`. **This deletes the data volume** (the
SQLite database and the stored credential), the backend container, and the local extension image.
Rebuild the image to install again. Cluster-side access is not touched: revoke it separately.

## First use

```bash
./scripts/cluster-up.sh                     # create or reuse the kind cluster (unchanged from the Docker setup)
./scripts/extension-cluster-access.sh       # explicit setup: read-only access, asks before changing the cluster
```

Then open **Resilience Engine** in Docker Desktop, choose **Connect cluster**, and pick
`.kube/extension-readonly.kubeconfig`. Until a cluster is connected the Deployment Topology page
shows the app's built-in sample topology, as it does outside Docker.

## The cluster credential

`scripts/extension-cluster-access.sh` is the only thing that changes the cluster, and only when you
run it and confirm. It creates `namespace/resilience-engine`, a service account, and a cluster role
(`deploy/rbac/resilience-readonly.yaml`) that allows **only `get`/`list` on nodes, pods and
deployments**; it deploys nothing and modifies nothing else. It then issues a **time-limited bearer
token** (default 168 h, `--duration 24h` for shorter) and writes the kubeconfig file, mode 600.

- **Renew:** run the script again and pick the new file. The page shows the expiry and flags an
  expired credential. Older tokens stay valid until they expire.
- **Revoke:** `./scripts/extension-cluster-access.sh --revoke` deletes the service account's access
  objects, which invalidates every issued token immediately. Then use **Disconnect cluster**.
- **Trade-off:** anyone who can read the file (or the extension's data volume) can read this cluster's
  nodes, pods and deployments until the token expires or is revoked. They cannot change anything or
  read secrets. A shorter `--duration` reduces that window at the cost of more frequent renewal.
- **The backend refuses anything stronger.** On upload it asks the cluster what the credential may do
  and rejects it if it can create or delete pods, patch or create deployments, delete nodes, read
  secrets or create role bindings. It also rejects kubeconfigs that disable TLS verification, use a
  proxy, run an exec plugin, or reference local files. The accepted file is rewritten to a minimal
  form; contents are never logged or returned.

## Security model

| Property | Result |
|---|---|
| Backend listens on a TCP port | **No.** The only TCP listener in the installed container is Docker's embedded DNS (127.0.0.11); no published ports. |
| Reachable from kind nodes | **No.** In the installed extension, a control listener on the same Docker network was reachable from both kind nodes by container IP and via the published host port (HTTP 200); the backend, probed identically, was not. Tested from the kind node containers; pods leave the cluster through those nodes but were not tested separately. |
| Reachable from other local users | **No.** Docker Desktop mirrors the socket onto the host under `~/Library/Containers/com.docker.docker/Data/ext-sockets/`; `~/Library` and `~/Library/Containers` are mode 700. |
| Reachable from processes running as you | **Yes, unauthenticated** (tested with `curl --unix-socket`). See residual risks. |
| Backend process | UID 10001, no Linux capabilities, `no-new-privileges`, not privileged; the container drops all capabilities except CHOWN/SETUID/SETGID needed to switch user (read back from the installed container). |
| Cluster credential | Read-only and time-limited (tested: 3 reads allowed; create/delete/patch, secrets, configmaps, namespaces all denied with 403). |
| Cluster-admin credentials | Refused at upload (tested with the admin kubeconfig). |
| Kubeconfig on disk | Normalised copy, mode 600 in a 700 directory, owned by the app user, on the extension's volume. |
| Automatic changes | None. The app has no Kubernetes write calls; startup only migrates the database and serves the socket. |

Residual risks, stated plainly:

- **Anything with access to the Docker Engine can reach the socket or the volume.** Docker Desktop
  extensions run with your user's permissions and are not sandboxed from each other or from Docker.
  Only install extensions you trust.
- The backend authenticates nobody: whoever can reach the socket can use the API. Inside the VM the
  socket directory is private to the app user. **On the host, Docker Desktop mirrors the socket to a
  path that any process running as your macOS user can open**, so local malware or any tool you run
  can call the API (read the app's views, record approvals, connect or disconnect the cluster
  credential, and read what the stored read-only token can read). That is the same trust level as
  your own `docker`, `kubectl` and `~/.kube/config`, and it is not reachable from the kind cluster
  or from ordinary containers, which do not mount `/Users`. It is not a defence against code
  running as you.
- Running the same stack from `compose.yaml` (the earlier Docker setup) is separate and still
  publishes `127.0.0.1:8000` with no authentication. Do not run both at once if you want only the
  socket-only backend.

## Data

SQLite (`/data/resilience.db`) and the stored credential live on the named volume
`k8s-resilience-engine-desktop-extension_resilience-engine-data`. Tested in the installed extension:

| Event | Database and credential |
|---|---|
| Backend container restart | kept (same container, same volume; migrations do not re-run) |
| `docker extension update` | kept (new container and image, same volume; 0 migrations re-run) |
| `docker extension uninstall` | **deleted** (volume removed); a reinstall starts empty and migrates itself |

## How Docker Desktop loads the UI (found by running it)

These behaviours of Docker Desktop 4.95.0 shaped the design. Each one broke the first build.

- **The UI is a `file://` page, and the SDK exists only on pages loaded with Docker Desktop's query string.**
  The entry page is opened as `file:///…/ui/index.html?platform=…&extensionName=…&hasBackend=true&backendSocket=…`
  and `window.ddClient` is present there. A page opened without that query string has no SDK, so
  `fetch` fails with "Failed to fetch". `shim.js` therefore appends the entry query string to links
  between the extension's own pages.
- **The SDK drops error bodies.** Any non-2xx answer is thrown as `{message: "HTTP 422: Unprocessable
  Entity", statusCode: 422}`; the backend's `{"detail": …}` is lost. When the shim sends the header
  `X-Resilience-Envelope: 1`, `extension/error_envelope.py` returns errors as a 200 whose body carries
  the real status and body, and the shim restores them. Requests without the header, and successful
  responses, are unchanged.
- **The tab can load before the backend is listening.** After install, update or restart the tab
  reloads immediately, but Alembic and `uvicorn` need about four seconds. The SDK then fails with
  `other side closed`. The shim retries `GET`/`HEAD` for about six seconds (inside the pages' own
  8 s timeout) when the failure looks like a connection failure; writes and real HTTP errors are
  never retried.
- **Docker Desktop mirrors the backend socket onto the host** (see Security model).
- **Uninstall deletes the data volume** (see Data).
- The Docker Desktop renderer console is not written to a log file, so UI behaviour cannot be read
  from Docker's logs. It was verified with a temporary in-tab diagnostic (below).

## Development

```bash
node --test extension/ui-src/shim.test.mjs      # fetch adapter logic (mocked SDK, no dependencies)
docker run --rm --entrypoint sh -v "$PWD/tests:/app/tests:ro" -e DATABASE_URL=sqlite:////tmp/test.db \
  k8s-resilience-engine:0.1.0 -c 'grep -iE "^(pytest|iniconfig|pluggy|pygments)==" requirements.txt | pip install -q -r /dev/stdin && python -m pytest -q -p no:cacheprovider'
docker extension dev debug k8s-resilience-engine:0.1.0     # opens the extension's developer tools
```

The UI uses the `window.ddClient` object Docker Desktop injects, as Docker's own `vm-service` sample
does, instead of the `@docker/extension-api-client` npm package. That keeps the project free of npm
dependencies and a bundler. If a future Docker Desktop stops injecting it, switch `shim.js` to the
package.

## Limitations

- **A pre-existing bug in the Recommendations page was fixed in a separate commit**, because it
  stopped the page loading anywhere (also in the plain browser app): `recommendations.js` passed its
  `approvalBase` URL prefix to `fetchAll()`, which requested `GET /api/recommendations` (404). The fix
  is four lines in `app/static/js/components/recommendations.js`; it belongs to Member 4's area and is
  isolated in its own commit so it can be reviewed or dropped independently.
- Components 3 and 4 still serve sample data; there is no recommendation apply or validation step.
  Nothing here applies Kubernetes changes, runs chaos experiments or deploys workloads.
- The live topology of a fresh kind cluster shows its three worker nodes and no services.
- kind workers are containers on one Docker host: logical failure domains, not independent machines.
- Component 1's pages default to `localhost` Jaeger and workload URLs, which do not point at anything
  useful from the extension backend. Jaeger-dependent features report that Jaeger is unreachable.
- The container healthcheck runs as root, which cannot enter the app-private socket directory, so it
  drops to the app user with `setpriv` (this was a bug in the first build: health stayed "starting").
- Not ready for the Marketplace: `docker extension validate` still reports the missing icon URL,
  screenshots, publisher URL, a multi-platform build, and that the tag must be semver (use one).
  Only the host architecture was built.

## Verification status

Checked in Docker Desktop 4.95.0 (macOS, Apple silicon, host architecture only).

**How the UI was verified.** Docker Desktop's tab cannot be driven by a browser tool and its console
is not logged, so a temporary diagnostic script (never part of the repository or of the shipped
image) was layered onto the image. Inside the real tab it walked every page by clicking the sidebar
links, read the rendered DOM, drove the interactive flows through the page's own controls, and
reported over a loopback-only listener that was stopped afterwards. This verifies what is on the
page, not how it looks: **layout and visual quality were not reviewed**.

| Check | Status |
|---|---|
| Image builds; `docker extension validate` accepts `metadata.json` | Done |
| `docker extension install`, `update`, `uninstall`, reinstall | Done |
| Backend container configuration as Docker Desktop created it | Done (`docker inspect`) |
| Every page loads in the tab with the SDK present, reached by clicking links | Done |
| Overview, Deployment Topology, Failure Impact, Recommendations show real API results (metric values, tables, sample content), no error states, no console errors | Done |
| Workflows page shows its own "trace service is unavailable" message (Jaeger is not running) with no errors | Done |
| GET over the SDK; POST through the UI (Request Approval → `PENDING_APPROVAL`, Approve/Reject offered, nothing applied) | Done |
| Error reporting: backend messages reach the page (invalid kubeconfig → "The kubeconfig needs a current-context.") | Done |
| Connect page: not-connected state, connected state (server, credential, expiry), Disconnect | Done |
| Deployment Topology live view shows the three real worker nodes, none of the sample nodes | Done |
| Requests after more than 5 s idle (stale keep-alive) | Done, all pages |
| First load racing a starting backend (install/update) | Done: pages recover via the retry |
| No application TCP listener; unreachable from kind nodes (same-network positive control) | Done (final build) |
| Host socket: works for the same user, parents mode 700, absent inside kind nodes | Done |
| Read-only RBAC (3 reads allowed, writes and secrets denied); admin credential refused | Done |
| SQLite and credential persistence across restart and update; automatic migrations; uninstall deletes data | Done |
| Python suite and fetch-adapter tests | Done (see the commit message for counts) |
| Picking a **valid** kubeconfig through the OS file dialog in the UI | **Not run** (needs a human at the dialog). The same upload was verified against the backend, and an invalid file through the real file input. |
| Clicking Approve/Reject, and `alert()` dialogs raised by the approval UI | **Not run** (deliberately: no approvals are decided) |
| Linux and Windows hosts; amd64 image; Marketplace publishing | **Not run** |
