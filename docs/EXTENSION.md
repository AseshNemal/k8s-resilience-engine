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

## Install, update and remove

The extension is built from this repository and installed from a **local image**. Nothing is
published to Docker Hub or the Marketplace, and nothing needs a registry account.

### Prerequisites

- Docker Desktop with extensions enabled (tested on 4.95.0, macOS, Apple silicon).
- **Settings → Extensions → untick "Allow only extensions distributed through the Docker
  Marketplace".** Without this, a local image is refused. This is your security setting and, on
  managed machines, may be locked by policy.
- No Python, Node or `kubectl` is needed to run the extension itself. `kind` and `kubectl` are needed
  only for the optional test cluster ([`DOCKER.md`](DOCKER.md)) and the cluster-access script below.

### Install (first time)

```bash
docker build -f extension/Dockerfile -t k8s-resilience-engine:0.1.4 .   # run from the repository root
docker extension install k8s-resilience-engine:0.1.4
docker extension ls                                                    # VM should say Running(1)
```

Docker asks you to confirm the install; answer `y` (or add `-f` to skip the question). Open
**Resilience Engine** in Docker Desktop's left sidebar. The tag must be a semantic version
(`0.1.4`, not `latest`); it is also the version Docker Desktop shows. The first start takes a few
seconds while the database is created and migrated; the pages retry on their own meanwhile.

### Update (after changing code or pulling `main`)

Rebuild and update whenever anything the image contains changes: `app/`, `extension/`,
`requirements.txt`, `sample_data/`, `alembic/`, or `experiments/`. `git pull` alone changes nothing
inside an installed extension.

```bash
# 1. Optional but recommended: run the tests against the new image (see Development below).
docker build -f extension/Dockerfile -t k8s-resilience-engine:0.1.5 .   # bump the version each time
docker extension update k8s-resilience-engine:0.1.5
docker extension ls                                                    # shows the new version, VM Running(1)
```

- Use a **new tag for every update**. That is what was tested; reusing a tag was not. The script picks it
  for you (see "How versions work" below).
- `update` removes and reinstalls the extension (container and image are replaced). **The data
  volume is kept, so your database, approval records and stored credential survive**; new Alembic
  migrations run automatically on the next start.
- The open tab reloads itself. Wait about five seconds if a page briefly says it cannot reach the
  backend; reads are retried automatically. Writes are not retried, so repeat a failed action by hand.
- Old image tags stay on disk when you update by hand: Docker never deletes a tagged image you built.
  `extension-manage.sh update` removes them for you after a successful update, keeping the new version and
  the one it replaced so a rollback is one command (`docker extension update k8s-resilience-engine:<old tag>`).
  Pass `--keep-all` to keep every image, or use `extension-manage.sh clean`.
- **If the read-only role changed between versions** (it now also reads services and replicasets), the
  stored grant no longer matches. Connect cluster shows "Access needs updating" and renewal pauses until
  you click **Set up access** and approve once; the dialog lists exactly the role update.

### One command: `scripts/extension-manage.sh`

The build, install, update and clean-up steps above are wrapped in one script, run from the repository
root. It only manages the Docker extension and its image; it never touches your cluster or its access
objects, and every command accepts `--dry-run` to print what it would run.

It works the same on macOS, Linux and Windows. `scripts/extension-manage.sh` (macOS, Linux) and
`scripts\extension-manage.cmd` (Windows) are launchers for `scripts/extension_manage.py`, which needs
Python 3.9 or newer and the Docker CLI; `check` also needs Go and Node and lists any that are missing
instead of skipping their tests. Before building it asks Docker for its memory: below 3 GB (override with
`RESILIENCE_MIN_DOCKER_MEMORY_GB`) it prints a warning, and if Docker does not answer within 30 seconds it
stops and says the Docker VM may be out of memory.

| Command | What it does |
|---|---|
| `install [--version X.Y.Z]` | Run the tests, build, and install. Refuses if already installed. |
| `update [--version X.Y.Z] [--keep-all]` | Run the tests, build the version for this commit, and replace the installed one. The data volume is kept. Afterwards the older local images are deleted, keeping the new one and the previous one (`--keep-all` keeps everything). Does nothing if this commit is already installed. |
| `uninstall` | Remove the extension after asking. Remove cluster access separately with `extension-cluster-access.sh --revoke`. |
| `status` | Installed version, VM state, the commit and branch it was built from, whether that matches this folder, and the local image tags. |
| `check` | Run the Python, Go and Node suites without building. |
| `clean` | Delete old local `X.Y.Z` image tags after asking. The installed tag is never removed; tags such as `0.1.5-qs` are left alone. |

Other options: `--skip-checks` (build without running the tests first), `--keep-all` (do not delete old
images after an update) and `--yes` (no prompts). The extension's backend container is hidden from
`docker ps`, so there is no `logs` command.

#### How versions work

With no `--version`, the script follows git, so everyone on the same commit builds the same version:

| Situation | Version |
|---|---|
| Clean checkout, number not used here yet | `0.1.<number of commits>`, for example `0.1.214`. After a `git pull` it moves forward by itself |
| Uncommitted changes, or that number is already taken | The next number nobody has used, because Docker keeps running the old image when a tag is reused |
| Not a git checkout, or git missing | The next patch above the installed version and every local tag |
| `--version X.Y.Z` | Exactly that |

Every image is stamped with the commit it was built from (`-dirty` is added when uncommitted changes
were included) and the branch, as the image labels `org.opencontainers.image.revision` and
`org.opencontainers.image.ref.name`. **The number alone does not identify the code**: two branches with
the same number of commits get the same number. `extension-manage.sh status` prints both, for example

```
Built from : commit aaaaaaa on integration/team-sync
This folder: commit 16c62a1 on feature/x
            It differs from the installed extension: run 'update' to install this folder.
```

so compare the commit and branch, not just the number, and only compare numbers between people on the
same branch. `update` rebuilds when the installed image came from a different commit, even under the same
number, and the number can go down when you switch to a branch with fewer commits.

### Check that it is healthy

```bash
docker extension ls                                       # version, UI tab, VM Running(1)
docker ps -a --filter label=com.docker.compose.project=k8s-resilience-engine-desktop-extension   # status should end in (healthy)
docker logs --tail 50 k8s-resilience-engine-desktop-extension-service         # migrations + "Uvicorn running on unix socket"
docker inspect -f '{{.HostConfig.PortBindings}}' k8s-resilience-engine-desktop-extension-service   # expect map[]
```

Docker Desktop hides extension containers from `docker ps`, including a `--filter name=…` search
(both returned nothing in testing, although the container was running). Only the compose-project label
filter above lists it. `docker logs` and `docker inspect` work by container name. A healthy install has
no published ports (`map[]`).

### Remove

```bash
./scripts/extension-cluster-access.sh --revoke            # optional: invalidate the cluster token first
./scripts/extension-manage.sh uninstall                   # also removes the background renewal job
```

`extension-manage.sh uninstall` runs `resilience-helper agent-disable` first, so the macOS background job
never outlives the helper binary it runs. (Plain `docker extension uninstall` does not know about the job;
if you used it, run `~/Library/Containers/com.docker.docker/Data/extensions/k8s-resilience-engine/host/resilience-helper agent-disable`
before removing the extension, or delete `~/Library/LaunchAgents/com.sliit.resilience-engine.renew.plist`.)

**Uninstalling deletes the data volume** (the SQLite database and the stored credential), the backend
container, and the local extension image. Rebuild the image to install again. Cluster-side access is
not touched by uninstalling: revoke it separately with `--revoke`.

### Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Install says the extension is not from the Marketplace | Untick "Allow only extensions distributed through the Docker Marketplace" (Prerequisites). |
| A page shows "Failed to fetch" right after install, update or Docker Desktop start | The backend is still starting. Wait about five seconds and reload the tab. Persisting longer: check `docker logs` and the health command above. |
| Page keeps failing and `docker ps` shows the backend `unhealthy` or restarting | Read `docker logs`. A failed migration or an unreadable volume prints the reason. Do not delete the volume to "fix" it unless you accept losing the database and credential. |
| Deployment Topology says "No cluster connected" (`CLUSTER_NOT_CONNECTED`) | No cluster is connected. Run `./scripts/extension-cluster-access.sh`, then **Connect cluster** and pick `.kube/extension-readonly.kubeconfig`. The app never shows sample topology in its place. |
| "This credential can change the cluster (…)" | Working as intended: only the read-only file from the script is accepted. Do not upload your personal or the kind admin kubeconfig. |
| "Could not reach the cluster API server" after Docker Desktop restarted | Re-run `./scripts/cluster-up.sh` (restarts the kind nodes; the published API port can change), then `./scripts/extension-cluster-access.sh` and pick the new file. |
| "The cluster rejected the credential. It may have expired." or the page flags the credential as expired | The token reached its time limit or was revoked. Run `./scripts/extension-cluster-access.sh` again and pick the new file. |
| Update did not change the interface | The image was not rebuilt, or the same tag was reused. Rebuild with a new tag and run `docker extension update`. |
| Port 8000 is in use / `compose.yaml` and the extension both running | They are separate. The extension never uses port 8000; stop the `docker compose` stack if you want only the socket-only backend. |

## First use

```bash
./scripts/cluster-up.sh                     # create or reuse the kind cluster (unchanged from the Docker setup)
./scripts/extension-cluster-access.sh       # explicit setup: read-only access, asks before changing the cluster
```

Then open **Resilience Engine** in Docker Desktop, choose **Connect cluster**, and pick
`.kube/extension-readonly.kubeconfig`. Until a cluster is connected every page that needs the cluster
says so explicitly (`CLUSTER_NOT_CONNECTED`); the app never substitutes sample data.

## Automatic renewal

A credential the extension set up itself (not one uploaded by hand) renews with no button and shows no
expiry. The token lasts up to **7 days** (what the cluster actually grants is what counts) and is renewed
once **2 days** remain, so a long weekend with the laptop shut cannot let it lapse. Three things renew it:

1. **The open extension** checks every 60 seconds (every 20 while a renewal is pending) and when the
   tab becomes visible.
2. **A macOS background job** (a per-user LaunchAgent, `com.sliit.resilience-engine.renew`, no
   administrator rights) runs `resilience-helper renew-managed` every 15 minutes. It does nothing unless
   the token is due. It is installed **only after you approve it in the native dialog** (the setup dialog
   names it; a connection set up earlier shows a one-time *Turn on background renewal* action), is
   removed when you remove the access or uninstall with `extension-manage.sh`, and logs to
   `~/Library/Logs/ResilienceEngine/renew.log`. Windows and Linux have none yet.
3. **A page that loaded while the token was expired** presses its own *Retry* the moment a renewal
   succeeds, so it fills in without a click.

Every renewal, from either path, only issues a read-only token: it shows no dialog, makes no write to the
cluster, and **never recreates removed or changed access**. If the access was removed, changed or widened,
the page says so and waits for you to approve again. That is the one case that needs a person.

When Docker Desktop is overloaded, `docker exec` alone can take 30 seconds. The helper therefore allows a
renewal 4 minutes, every call in the page has a watchdog (a call that never answers cannot stall the loop
forever), and a run that still runs out of time is reported as `too-slow` and retried with back-off rather
than as "backend unavailable". Closing unused kind clusters or giving Docker more memory makes it faster.

## The cluster credential

`scripts/extension-cluster-access.sh` is the only thing that changes the cluster, and only when you
run it and confirm. It creates `namespace/resilience-engine`, a service account, and a cluster role
(`deploy/rbac/resilience-readonly.yaml`) that allows **only `get`/`list` on nodes, pods, services,
deployments and replicasets**; it deploys nothing and modifies nothing else. It then issues a **time-limited bearer
token** (default 168 h, `--duration 24h` for shorter) and writes the kubeconfig file, mode 600.

- **Renew:** a credential added by hand never renews itself: run the script again and pick the new file.
  The page shows its expiry and flags it when expired. (A credential set up from *Connect cluster* renews
  itself; see Automatic renewal.) Older tokens stay valid until they expire.
- **Revoke:** `./scripts/extension-cluster-access.sh --revoke` deletes the service account's access
  objects, which invalidates every issued token immediately. Then use **Disconnect cluster**.
- **Trade-off:** anyone who can read the file (or the extension's data volume) can read this cluster's
  nodes, pods, services, deployments and replicasets until the token expires or is revoked. They cannot change anything or
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

## Interface

The pages are the app's own, restyled to sit inside Docker Desktop. All of it lives in the extension
(`extension/ui-src/docker.css`, `chrome.js`, `connect.js` and `build_ui.py`); nothing under `app/` is
edited, so the standalone web app looks exactly as before.

- **Colors, type and weights are Docker's own**, read from the theme object Docker Desktop injects into
  extension pages (`window.__ddMuiV5Themes`, Docker Desktop 4.95.0): primary `#1D63ED` / `#2986FF`,
  paper `#FFFFFF` / `#10151B`, secondary text `#677285` / `#7794AB`, Roboto with the system fallback,
  14px base, weights 400 and 500 only. One rule set uses CSS `light-dark()` and follows
  `prefers-color-scheme`, which in the extension tab tracks Docker's appearance (checked: the tab's
  background was exactly `#10151B` in dark). Docker sets no theme class or attribute on the frame, so
  there is nothing else to listen to.
- **Navigation** is a compact text list (sentence case, labels wrap instead of truncating) with the
  current page highlighted; there is no brand block because the Docker tab already names the
  extension. Below about 820px it becomes a tab strip.
- **Header**: the page title, and one cluster chip (not connected, connected read-only, credential
  expired) that links to Connect cluster. The decorative badges and refresh time are hidden; the health
  badge appears only if the backend is unreachable.
- **Live vs unavailable** is stated in the same place on every page: the data-source chip shows whether
  the figures come from the connected cluster (complete, partial, or connected with no workloads), or that
  nothing is shown because the cluster could not be read. No metric is invented.
- **Overview** shows connection status first, then the existing metrics as one continuous strip, the
  analysis, and the components as a list with "Open component" links.
- **Recommendations** keeps its comparison table and approval gate, adds a plain note ahead of the
  gate (*Approving or rejecting only records your decision. Nothing is applied to your cluster.*), and
  the approval dialog now behaves as a dialog (see below).
- **Connect cluster** is one status card (state, server, credential, access, expiry, Disconnect) and
  one step (choose the file, Connect or Replace credential). Errors show the backend's message plus the
  next step.
- **Workflow dependencies** keeps all of its content; the oversized intro is a quiet note and its
  hardcoded light panels use the theme so they stay readable in dark.

Two things the app never styled are defined here: the approval **dialog** (`.modal-backdrop`/`.modal`,
which made it appear permanently on the page instead of only when asked) and the primary, danger and
form-field controls. The dialog is hidden until requested, closes on Escape and Cancel, and opening or
cancelling it changes nothing. These styles exist only in the extension; the standalone app still lacks
them (`app/static/css/` is not touched).

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
node --test extension/ui-src/shim.test.mjs extension/ui-src/chrome.test.mjs   # adapter and header wording (no dependencies)
docker run --rm --entrypoint sh -v "$PWD/tests:/app/tests:ro" -v "$PWD/extension:/app/extension:ro" \
  -e DATABASE_URL=sqlite:////tmp/test.db k8s-resilience-engine:0.1.3 -c 'grep -iE "^(pytest|iniconfig|pluggy|pygments)==" requirements.txt | pip install -q -r /dev/stdin && python -m pytest -q -p no:cacheprovider'
docker extension dev debug k8s-resilience-engine     # turns on debug mode for the extension; see: docker extension dev --help
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
  isolated in its own commit so it can be reviewed or dropped independently. (The page script has since
  been reworked for real-data-only operation.)
- Component 3 (Failure impact) runs its deterministic prediction over the connected cluster's live
  topology and an approved, frozen workflow from Component 1, and refuses (`TOPOLOGY_INCOMPLETE`,
  `WORKFLOW_EVIDENCE_UNAVAILABLE`) when that evidence is missing. Component 4 has no live recommendation
  source yet, so the Recommendations page says so (`RECOMMENDATION_EVIDENCE_UNAVAILABLE`) and no approval
  can be requested; there is no recommendation apply or validation step.
  Nothing here applies Kubernetes changes, runs chaos experiments or deploys workloads.
- The live topology of a fresh kind cluster shows its three worker nodes and an empty (valid) result with no services.
- Service/ownership resolution follows Kubernetes (Service selector, then pod → ReplicaSet → Deployment), so
  applications whose Deployments are named differently from their `app` label (Istio Bookinfo's
  `productpage-v1`) resolve completely. This is covered by tests on a Bookinfo-shaped fake cluster and has not
  been verified against a real Bookinfo cluster yet. A pod that a Service selects but no Deployment owns
  (a StatefulSet's pod) leaves that service `partial`, so a prediction that depends on it is refused.
- **The live recommendation pipeline is incomplete** (see `docs/RECOMMENDATION_LIVE_DATA.md`): nothing yet
  produces a `RecommendationReport`, YAML proposal or `ValidationReport` from live evidence.
- An installed extension keeps running the image it was installed from. Changes only appear after
  rebuilding and running `docker extension update`.
- kind workers are containers on one Docker host: logical failure domains, not independent machines.
- Component 1's pages default to `localhost` Jaeger and workload URLs, which do not point at anything
  useful from the extension backend. Jaeger-dependent features report that Jaeger is unreachable.
- The container healthcheck runs as root, which cannot enter the app-private socket directory, so it
  drops to the app user with `setpriv` (this was a bug in the first build: health stayed "starting").
- Not ready for the Marketplace: `docker extension validate` still reports the missing icon URL,
  screenshots, publisher URL, a multi-platform build, and that the tag must be semver (use one).
  Only the host architecture was built.

## Verification status

**Automatic renewal (7-day tokens, background job, watchdogs).** Verified by Go, Node and Python unit
tests, and by a real macOS check that `plutil` accepts the generated plist and that launchd loads it
(`run interval = 900 seconds`, run at load) and unloads it again, using a scratch label and `/usr/bin/true`.
Not yet verified: the background job renewing a real expired credential end to end through Docker Desktop,
and anything on Windows or Linux.

Checked in Docker Desktop 4.95.0 (macOS, Apple silicon, host architecture only).

> Everything in this section was verified **before the real-data-only change** and has not been re-run
> against it. Treat the rows as the record of that earlier build, not of the current code.

**How the UI was verified.** Docker Desktop's tab cannot be driven by a browser tool and its console
is not logged, so a temporary diagnostic script (never part of the repository or of the shipped
image) was layered onto the image. Inside the real tab it walked every page by clicking the sidebar
links, read the rendered DOM, drove the interactive flows through the page's own controls, and
reported over a loopback-only listener that was stopped afterwards. This verifies what is on the
page and which styles apply, not how it looks in Docker's window.

**Interface checks (after the redesign).** The interface itself was reviewed from real Chrome renders
(the same Chromium engine as Docker Desktop's Electron) of the exact built UI files, with a mock of the
SDK that reproduces its behaviour:

| Check | Result |
|---|---|
| All six pages, light and dark, at a wide width (about 1100px) | Reviewed from renders |
| Approval dialog open, in both themes; hidden by default; Escape and Cancel close it; approval stays pending | Verified (renders and DOM) |
| Connect cluster: empty state, connected state (faked status), an error with its next step | Reviewed from renders |
| No page-level horizontal overflow at 420px and 560px on all six pages | Measured |
| In the real Docker Desktop tab: Docker's stylesheet applied, background exactly Docker's dark `#10151B`, forcing light and dark gives Docker's two paper/text pairs, no hidden element showing, no error states, real data on every page, dialog and Connect error flows, a 480px frame fits with the tab-strip navigation | Verified (DOM and computed styles) |
| **Layout geometry in Docker Desktop's own window** (nav label truncation, title width, real viewport size, dialog placement) | **Not verified.** The extension view was never on screen during the automated runs (its viewport reported 0x0), so those numbers are void. |
| **Screenshots of Docker Desktop's window**; Docker's real light appearance (only forced, not selected in Docker) | **Not available** (macOS denies screen capture to the tooling). Please check by eye. |

| Check | Status |
|---|---|
| Image builds; `docker extension validate` accepts `metadata.json` | Done |
| `docker extension install`, `update`, `uninstall`, reinstall | Done |
| Backend container configuration as Docker Desktop created it | Done (`docker inspect`) |
| Every page loads in the tab with the SDK present, reached by clicking links | Done |
| Overview, Deployment Topology, Failure Impact, Recommendations show real API results (metric values, tables, sample content), no error states, no console errors | Done (before the real-data-only change; not re-run since) |
| Workflows page shows its own "trace service is unavailable" message (Jaeger is not running) with no errors | Done |
| GET over the SDK; POST through the UI (Request Approval → `PENDING_APPROVAL`, Approve/Reject offered, nothing applied) | Done before the real-data-only change; **superseded**: a request now needs a live recommendation (`RECOMMENDATION_EVIDENCE_UNAVAILABLE` until Member 4's pipeline exists) |
| Error reporting: backend messages reach the page (invalid kubeconfig → "The kubeconfig needs a current-context.") | Done |
| Connect page: not-connected state, connected state (server, credential, expiry), Disconnect | Done |
| Deployment Topology live view shows the three real worker nodes, none of the sample nodes | Done before the real-data-only change; not re-run |
| Requests after more than 5 s idle (stale keep-alive) | Done, all pages |
| First load racing a starting backend (install/update) | Done: pages recover via the retry |
| No application TCP listener; unreachable from kind nodes (same-network positive control) | Done (final build) |
| Host socket: works for the same user, parents mode 700, absent inside kind nodes | Done |
| Read-only RBAC (3 reads allowed, writes and secrets denied); admin credential refused | Done |
| SQLite and credential persistence across restart and update; automatic migrations; uninstall deletes data | Done |
| Python suite and adapter/chrome tests | Done (see the commit messages for counts) |
| Picking a **valid** kubeconfig through the OS file dialog in the UI | **Not run** (needs a human at the dialog). The same upload was verified against the backend, and an invalid file through the real file input. |
| Clicking Approve/Reject, and `alert()` dialogs raised by the approval UI | **Not run** (deliberately: no approvals are decided) |
| Linux and Windows hosts; amd64 image; Marketplace publishing | **Not run** |
