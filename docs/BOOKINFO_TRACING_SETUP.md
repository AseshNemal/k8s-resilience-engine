# Bookinfo tracing setup: Plan, Enable, Verify, Undo

Status: **built and tested against an in-memory cluster; never run against a real cluster.** The configuration lives in
`k8s-bookinfo/tracing/` (branch `tracing/prepare-istio-otel`); the extension carries reviewed copies. Nothing has been
installed, no Pod restarted, and the running extension has not been updated.

Goal for the first research presentation: real, correlated traces of `productpage -> details` and
`productpage -> reviews-v2 -> ratings`, produced by Istio sidecars from real requests and received by the Jaeger container
the extension already manages. No second Jaeger, no synthetic spans, no change to Members 1 to 4.

## What exists

| Piece | Where |
| --- | --- |
| Plan (read-only), approval fingerprint, gates, downtime disclosure | `extension/helper/internal/bookinfotrace` |
| Apply, rollback, Undo, Verify, record, pinned `istioctl`, allow-list writer | `extension/helper/internal/traceinstall` |
| Raw create / UID-guarded delete / label patch (used by nothing else) | `extension/helper/internal/kube` (`RawWriter`) |
| Commands `trace-bookinfo-plan`, `-apply`, `-undo`, `-verify`, `-status` | `extension/helper/main.go`, `main_bookinfo.go` |
| Four controls on the Connect page | `extension/ui-src/bookinfo-tracing.js`, `extension/build_ui.py` |
| Trace verdict incl. which services delivered spans | `app/integrations/tracing/trace_path.py` (`GET /_extension/tracing/trace-path`) |
| Reviewed manifests (copies, drift-tested) | `traceinstall/manifests/`, `traceinstall/data/istio-objects.json` |

## What one click does

**Plan** reads the cluster and the host and changes nothing: Kubernetes version (Istio 1.31 supports 1.32 to 1.37), the
`bookinfo` namespace and its four Deployments Ready, no existing Istio (never touched), no existing injection, the Jaeger
address re-discovered from inside the nodes and proved by a TCP connection, and memory. Blockers have stable codes; an
unreadable reading is a blocker, never a pass.

**Enable Bookinfo tracing** (Apply):

1. Platform check, plan, **native approval dialog** (cluster, every change in order, the Pods that restart and what is
   unavailable while they do, any download, and that a failed step rolls back what this run created).
2. The plan is made again and must have the same fingerprint; otherwise the approval is void and nothing is changed.
3. Without touching the cluster: `istioctl` 1.31.2 is fetched if absent (see below), the Istio objects are rendered
   offline and must equal the reviewed list of 32 exactly, the documents are prepared.
4. The record is written (installation ID, cluster identity, baseline, snapshot, full planned inventory) **before** the
   first write.
5. Stage 1: namespace `istio-system`, then the 32 Istio objects in dependency order, wait for istiod, **memory gate 1**
   (istiod under 300 MiB, Docker under 2.6 GiB, swap grew under 1 GiB).
6. Stage 2: the Jaeger address is checked again; `ServiceEntry`, `Telemetry`, then the injection label.
7. Stage 3: the three Pods are deleted **one at a time, leaf first** (ratings, details, productpage), each waiting for a
   Ready replacement that has a sidecar; then `reviews-v2` (from the unmodified original Deployment) with a sidecar.
8. Stage 4: `DestinationRule` and `VirtualService` (all reviews traffic to v2), **memory gate 2** (Docker under 3.2 GiB,
   every sidecar under its 128 MiB cap, every Pod Ready, swap growth).
9. Memory is also read every 15 s while waiting; a reading over the limit, or three unreadable readings in a row, stops
   the setup.

Any failed step, gate, timeout, cancellation or Ctrl+C **rolls back what this run created** (own deadline, not
cancelled with the run). If rollback cannot finish, the record stays, says what remains, and Apply refuses until Undo has
finished it.

**Verify**: re-discovers the Jaeger address and compares it with the `ServiceEntry` (drift is reported and nothing is
sent), requires a Ready sidecar on ratings, details, productpage and reviews-v2, checks memory, runs the temporary client
Pod inside the mesh (60 real requests to `productpage`; it makes no span and never talks to Jaeger) and deletes it again,
also on failure or cancellation. The page then reads Jaeger (`trace-path`) and says **Verified** only when one trace holds
`productpage->details`, `productpage->reviews` and `reviews->ratings` and no `reviews-v1` span is seen. The same answer
lists which of the four services delivered spans: a span of `ratings` in Jaeger was exported by the ratings Pod's own
sidecar, so it shows that Pod reached Jaeger. A node-level connection test cannot show that.

**Undo**: dialog, then in this order: client Pod, route, `reviews-v2` (only if this run created it), **injection label**,
the sidecar Pods recreated without sidecars (waiting until each workload is Ready again), `Telemetry`, `ServiceEntry`,
then Istio in reverse creation order (webhooks first, namespace last). The label comes off before Istio goes because the
injection webhook fails closed. If the label or a workload cannot be restored, Istio is left in place and the remainder
is reported. Finally the cluster is compared with the snapshot taken before (namespace labels, Deployment spec digests,
replicas, containers per Pod): the result must be identical.

## Downtime (disclosed in the dialog and in Plan)

One replica of each service, so nothing takes over while a Pod is replaced: `ratings` (reviews show "ratings
unavailable"), `details` (no book details), `productpage` (the web page does not load). Deleting a Pod, not
`rollout restart`, keeps the Deployment spec unchanged so Undo restores it exactly. How long each takes was **not
measured**; the setup waits up to 5 minutes per Pod before it rolls back.

## Credentials and authorization

* The extension's read-only credential, RBAC and backend are untouched and unused by these commands (a test asserts that
  no token request is made).
* The writes use **your own kubeconfig credential**, loaded by the helper for the length of one command, held in memory,
  and dropped. It is never stored, printed, logged, or passed to another process; `istioctl` runs without it
  (`KUBECONFIG` points at a missing file, `HOME` at a scratch directory, clean environment).
* The cluster must be a verified local cluster (kind or Docker Desktop, loopback address, every node reporting a kind
  provider ID); anything else is refused without contact.
* Writes exist only in `traceinstall`, and only `Guard` calls them (source-scan test). `Guard` allows: the reviewed
  inventory (exact kind, namespace and name), creates labelled with the management label and this installation's ID,
  deletes that carry the recorded UID (the API server refuses a mismatch), Pods the approved plan named (or sidecar Pods
  of the three restarted workloads), and the `istio-injection` label on `bookinfo` only. There is no generic patch.

## `istioctl` (managed by the helper)

Pinned to 1.31.2. Downloaded only from `https://github.com/istio/istio/releases/download/1.31.2/<asset>` (redirects only to
GitHub hosts, size-capped), accepted only if its SHA-256 equals the value compiled into the helper, extracted safely
(exactly one expected file; links, absolute paths, `..` and extras refused), stored privately with the verified hash and
re-checked on every use. It is used **only to render**; the helper creates the objects itself, so the inventory is exact.

| Platform | Asset | SHA-256 |
| --- | --- | --- |
| darwin/arm64 | `istioctl-1.31.2-osx-arm64.tar.gz` | `b5f57049a6fe4b8ac39f13a9f28fcdd2a8cbfac9a086be56d9f09f490765960f` |
| darwin/amd64 | `istioctl-1.31.2-osx.tar.gz` | `17be5cc9031d4a44a574e3170914a5ea705148b9e8af1c6ee38f065fdb8790ba` |
| linux/amd64 | `istioctl-1.31.2-linux-amd64.tar.gz` | `6ce061447857b54cc90314fd8a72f0f8ed234f67cb0d6e3fb3ef3739caaa5edb` |
| linux/arm64 | `istioctl-1.31.2-linux-arm64.tar.gz` | `62a493e287f2547397a6a7fc7d0fa2df91e041081800c9ca8b6bbfa3a4e23378` |
| windows/amd64 | `istioctl-1.31.2-win.zip` | `784791b0f8127ef6e3ab514da41821d0e0d69849e7847118864e4c44d2c45961` |

Other platforms (for example windows/arm64, for which Istio publishes none) are refused before any network use. The
checksums are those Istio publishes next to each asset (read 2026-10-22); only darwin/arm64 was also downloaded and
hashed here, and that render was checked against the reviewed 32-object list with the real binary.

## The record (survives restarts and extension upgrades)

`<user config dir>/resilience-engine/bookinfo-tracing/bookinfo-tracing-<cluster key>.json`: directory 0700, file 0600 on
macOS and Linux (on Windows the per-user profile's access list is what protects it; no more is claimed), written
atomically (temporary file, fsync, rename). It holds the installation ID, the **cluster identity** (context, API
address, CA fingerprint and the UID of `kube-system`, so a recreated cluster is another identity), the baseline readings,
the snapshot, the full inventory with each object's state and UID, the journal, the failure and what remains. A binding
digest ties identity, ID, baseline, snapshot and inventory together; a second digest covers the whole file. A record that
fails either, or has loose permissions, is refused. Every created object also carries the installation ID as a label.
These digests detect corruption and accidents; they are not a defence against someone who can already write as you.

## Platforms

Pure Go, no cgo; the helper vets and builds for linux/amd64, linux/arm64, darwin/amd64, darwin/arm64 and windows/amd64.
Windows reads swap from the page file through Windows PowerShell with a fixed script, and unknown stays unknown (it
blocks, it is never read as zero). **Only macOS (arm64) has been exercised on a real machine; the Windows and Linux paths
are unit-tested against fakes and compile-checked.** The approval dialogs on Windows and Linux carry the same caveat as
the existing connection flow.

## Security review

| Risk | Control | Evidence |
| --- | --- | --- |
| Cluster-admin credential kept or leaked | in memory for one command; not in the record, outputs, `istioctl`'s environment, or logs; no token requests; no RBAC created | boundary tests (no client/kubeconfig/token code in the package), secret scans of outputs and record, `TestNoClusterTextReachesAnyOutcome...` |
| Page or backend triggers a change | a native dialog the page cannot see; the page passes only a command name and a context that is matched against the real kubeconfig | helper tests: decline/timeout/no-dialog change nothing |
| Stale approval | fingerprint of the exact plan, re-planned after approval | `TestAnApprovalIsVoid...` |
| Writes outside the intended set | `Guard` allow-list, labels, UID preconditions, only Guard writes | guard and source-scan tests |
| Deleting someone else's object | ownership = installation label **and** recorded UID; mismatches are left alone | Undo ownership tests |
| Deleting someone else's Istio resources/CRDs/namespace | CRDs kept while any custom resource exists (or cannot be listed); namespace kept if it holds anything not created by istiod or this run; Plan refuses an existing Istio | Undo keep-tests |
| Tampered or substituted `istioctl` | pinned checksum, safe extraction, hash re-checked on use, GitHub-only, render must equal the reviewed list | istioctl tests, opt-in test with the real binary |
| Crash / kill / Ctrl+C mid-way | record before writes; kill at **every** write (before and after) is recoverable by Undo; cancellation still rolls back | tables of 84 + Undo kills |
| Bad rollback order stranding the namespace | label, then Pods, then Istio; the simulator fails closed like the webhook | order and stuck-Pod assertions |
| Memory exhaustion on an 8 GB Mac | gates 0/1/2 plus monitoring while waiting; unreadable = stop | gate and monitor tests |
| Injection through names | fixed argument lists; node names regex-checked; addresses parsed; PowerShell script constant; dialog text sanitised; page uses `textContent` and a field whitelist | dialog, sanitiser and JS tests |
| File exposure | owner-only modes, refused if loose | store tests |

**Residual risks (not controlled, stated plainly):**

1. The *content* of the rendered Istio objects (for example istiod's ClusterRole rules) is whatever the pinned, checksummed
   `istioctl` renders; names, kinds and count are enforced, but a digest of the rendered content is not pinned. Pin one
   per platform after the first successful run.
2. Container images are pinned by tag (`istio/pilot:1.31.2`, `istio/proxyv2:1.31.2`, `curlimages/curl:8.23.0`,
   `registry.istio.io/release/examples-bookinfo-reviews-v2:1.20.3`), not by digest.
3. Anyone able to run as you can alter your record, your kubeconfig, or the helper.
4. A hard kill leaves partial state until you press Undo (it is recorded and Apply refuses to start over it).
5. A TLS-inspecting proxy that is not trusted by the operating system makes the one-time `istioctl` download fail
   (safely); the helper never disables certificate verification.

## Not verified yet (what the first real run will settle)

* Creating the 32 objects with this client against a real API server (CRD establishment, webhook CA injection by istiod).
* That istiod accepts the external OTLP provider through the `ServiceEntry` and that a **Pod** reaches Jaeger.
* The measurements: `crictl stats` container names (`discovery`, `istio-proxy`) feed gates 1 and 2; if they cannot be read
  the gate fails and the setup rolls back (safe, but then the demo does not proceed).
* Real memory of istiod and sidecars; real Pod replacement times; span names and tags the sidecars emit.
* Windows and Linux hosts; the approval dialog on those systems; the Windows page-file reading.

## First demonstration (not done yet)

1. Docker Desktop with Kubernetes running; Bookinfo Ready (`bookinfo` namespace, four Deployments, one replica each).
2. Update the extension (`./scripts/extension-manage.sh update`; Windows `scripts\extension-manage.cmd update`) so the new
   helper and page are installed. This replaces the running extension.
3. Connect page: the Tracing service card running (Jaeger answers on 16686/4317/4318). Close heavy applications first.
4. Bookinfo card -> **Plan**. Read blockers, downtime and the Jaeger address. Fix anything blocking.
5. **Enable Bookinfo tracing** -> read the system dialog -> approve. First run downloads `istioctl` and pulls images:
   expect several minutes. Progress lines come from the saved record.
6. **Verify**. Expect "Verified" with edges and all four services listed; open Jaeger at http://localhost:16686.
7. **Undo** when finished (or to repeat); it ends with a comparison against the snapshot.

If anything goes wrong the setup removes what it created; if the page or Docker Desktop is closed mid-way, reopen it and
press Undo.

## Tests

Go: table tests of a failure at each of the 42 writes, a kill before and after each, a kill at each write of Undo, ownership
and keep rules, gates and monitoring, `istioctl` handling, record integrity, command wiring, boundary scans, and
cross-compilation. Python: embedded manifests identical to `k8s-bookinfo/tracing`, reviews-v2 identical to the original,
the trace verdict and services. Node: the controls, flows and field whitelist.
