# Bookinfo tracing setup: Plan, Apply, Verify, Undo

Status: **preparation**. The configuration is in `k8s-bookinfo/tracing/` (branch `tracing/prepare-istio-otel`). The
helper can **plan** (read-only) and the backend can **verify traces**. Applying and undoing are designed here and
deliberately **not built**. Nothing has been applied to any cluster.

Goal for the first research presentation: real, correlated traces of `productpage -> details` and
`productpage -> reviews-v2 -> ratings`, produced by Istio sidecars from real requests and received by the Jaeger
container the extension already manages. No second Jaeger, no synthetic spans, no change to Members 1 to 4.

## What exists today

| Piece | Where | State |
| --- | --- | --- |
| Istio 1.31.2 minimal install, `ServiceEntry`, `Telemetry`, reviews-v2 route, temporary curl Pod | `k8s-bookinfo/tracing/` | Written; validated offline with `istioctl 1.31.2` |
| Helper `trace-bookinfo-plan <context>` | `extension/helper/internal/bookinfotrace`, `main.go` | **Built, read-only**, tested |
| Exact preview, allow-list, memory gates, Jaeger address rediscovery, snapshot comparison, approval dialog text | `bookinfotrace` | Built and tested, **not connected to any writer** |
| Backend `GET /_extension/tracing/trace-path` | `app/integrations/tracing/trace_path.py` | Built, read-only, tested |
| Apply, Undo, Verify orchestration, UI buttons, snapshot storage, `istioctl` handling | | **Not built** |

There is no `trace-bookinfo-apply`, `-undo` or `-verify` command; a test fails if one appears before this design is
reviewed.

## Keeping the read-only credential read-only

The extension's Kubernetes credential is unchanged: a read-only service account on exactly five resources, with RBAC
mirrored and tested between the backend and the helper. This work adds no RBAC and edits none. It needs reads the
extension's account does not have (namespaces, CRDs, Pods' containers), so **the helper reads them with the person's
own kubeconfig, inside the helper process**, as it already does to set the extension up. Nothing from that credential
reaches the page, the backend, a log or an output.

Two layers keep reads from becoming writes:

1. `flow.ReadOnlyCluster` returns a `kube.Reader`, an interface with only `ServerVersion`, `Get` and `List`. Code
   that takes a `Reader` cannot call `Create`, `Update`, `Delete` or request a token, because the methods do not exist.
2. A test scans the `bookinfotrace` package for the names of the write calls, the writable client type and process
   execution, and fails if any appears. A command-level test runs the real plan against a recording fake cluster and
   asserts that every request was a `get` or `list` and that there were no writes and no token requests.

## The separate authorization mechanism for writes (design)

Applying is the only step that writes. It must not reuse the extension's credential, and it must not widen it. The
design:

1. **A fresh, ready Plan.** Apply recomputes the Plan itself. A Plan with any blocker cannot be applied.
2. **A native dialog the page cannot answer** (`ApplyDialog`), listing every step in order, the Pods that will be
   deleted by name, the memory gates, that Jaeger and the extension's read-only access are not changed, and that a
   failed gate removes only what the run created. A preview too long to show in full is refused, not truncated.
3. **Approval bound to the exact preview.** The approval carries a fingerprint of the preview (steps, Jaeger address,
   Pod names). If the cluster changed between Plan and Apply, the fingerprint differs and Apply refuses and asks for
   a new Plan. This is the same idea as the recommendation approval's proposal fingerprint.
4. **An allow-list in code, checked before every write** (`Allowed`, `AllowedPodDeletion`). It names only the objects
   in `changeset.json` in the `bookinfo` namespace, the injection label on that namespace, and the Pods the Plan
   named. Another namespace, RBAC, Secrets and the existing Bookinfo Deployments are refused whatever is asked.
5. **A writer that is not a `Reader`.** Writes go through a separate type that exists only in the Apply path, so the
   planning code can never reach it.
6. **Istio itself is installed by a pinned `istioctl`**, because it installs 32 objects, 15 CRDs and two webhooks in
   the right order. Version 1.31.2, checksum-verified against a value compiled into the helper, run with
   `--context <the approved context>` and the reviewed `install-minimal.yaml`. Only the checksum for macOS arm64 is
   known today (`b5f57049...`); other platforms need theirs before they are offered.
7. **Rollback on a failed gate is pre-authorized by the dialog and limited to what the run created.** A record of what
   this run created is kept so Undo removes exactly that. A reviews-v2 that was already deployed is never removed.

## Plan (built)

`resilience-helper trace-bookinfo-plan <context>` prints one JSON line with the usual `ok`/`state` and a `plan`:

* `preview`: the nine ordered steps, with the Jaeger address, the Pods to recreate and what is created;
* `blockers` (stable codes) and `warnings`; `ready` is true only with no blockers;
* `jaeger`: the address and the nodes that proved it; `baseline`: the readings; `gate0`: its decision.

Blockers: Kubernetes version unknown or outside Istio 1.31's 1.32 to 1.37; `bookinfo` namespace missing; Istio
already installed (namespace or CRDs found, so an existing Istio is never touched); namespace or Pod already injected;
a required Deployment missing, not Ready or of unknown readiness; the Jaeger address not discoverable or not
validated; Docker memory too tight or **any reading that could not be taken**. Heavy swap is a warning.

### Why the Jaeger address is rediscovered, never typed

Nothing is hardcoded. Every node (up to four) resolves `host.docker.internal` to an IPv4 address; they must all
agree; the address must be private (public, loopback, link-local, unspecified and multicast are refused); and every
node must open a TCP connection to port 4317 there. No bytes are sent. A static address is used in the `ServiceEntry`
so Pods do not depend on DNS or an IPv6 answer (the pod network is IPv4 only). Apply repeats the check just before
applying, and Verify rediscovers and reports **drift** if the `ServiceEntry`'s address no longer matches.

On the real machine (2026-10-22) all three nodes resolved `192.168.65.254` and reached ports 4317 and 4318; a port
with nothing behind it was refused, so the test can fail. A Pod itself has not been tested; that is part of Verify.

## Apply (designed)

Stages, each followed by its gate. A gate that fails, or a reading that cannot be taken, stops the setup and removes
what the run created.

| Stage | Steps | Gate after |
| --- | --- | --- |
| 0 | Plan; snapshot of the namespace labels, Deployment spec digests and Pod containers | Gate 0: baseline recorded, room for the worst case (about 1.3 GiB) |
| 1 | Install istiod (minimal) | Gate 1: istiod under 300 MiB, Docker under 2.6 GiB, swap grew by under 1 GiB |
| 2 | `ServiceEntry`, `Telemetry`, namespace label, recreate 3 Pods one at a time, create reviews-v2, then the route | Gate 2: Docker under 3.2 GiB, every sidecar under its 128 MiB cap, all Pods Ready, swap grew by under 1 GiB |

The Mac is already using 2.7 to 3.7 GiB of swap, so swap growth is gated as well as Docker memory. Pods are recreated by
**deleting them**, not by `rollout restart`, because a restart edits the Deployment's spec and Undo could then no
longer restore it exactly. The route goes in only after reviews-v2 is Ready, or requests for it answer 503.

## Verify (designed; the trace half is built)

1. Re-run the address check and report drift.
2. Create the temporary client Pod (already in the approved preview): 60 real requests to `productpage`; it makes no
   span itself and never talks to Jaeger.
3. Delete the Pod when it finishes.
4. The page then reads `GET /_extension/tracing/trace-path`, which examines real `productpage` traces in Jaeger.

`trace-path` reports which calls are linked: a child span whose parent belongs to another service. Services that merely
appear in a trace do not count, a reference to a span that is not in the trace is skipped, and a missing call is listed
as missing. The verdict is `VERIFIED` only if at least one trace holds all of `productpage->details`,
`productpage->reviews` and `reviews->ratings`, and **no span shows reviews-v1**, so routing has to be deterministic.
`reviewsVersion` counts traces whose span tags name v2, v1 or neither. Which tag carries the version is not assumed:
any tag value naming `reviews-v2` or an Envoy cluster `|v2|` counts, and the result is evidence, not proof.

The trace documents in the tests are inputs for the parser only. Nothing at runtime builds a span.

## Undo (designed)

Order matters, because the injection webhook has `failurePolicy: Fail`: while the namespace is labelled, Pods cannot be
created if istiod is gone, so the label is removed **before** Istio.

1. delete the client Pod if it exists; 2. delete the route; 3. delete reviews-v2 if this run created it;
4. remove the injection label; 5. delete the three Pods one at a time; 6. delete the `Telemetry` and `ServiceEntry`;
7. `istioctl uninstall --purge`, delete `istio-system`, remove leftover `*.istio.io` CRDs.

Then `Compare(before, after)` checks the result against the snapshot and must be empty: the same namespace labels, the
same Deployment specs **by digest** (a stray rollout-restart annotation shows up), the same replicas, and the same
containers in each Pod, so no sidecar is left. After Undo the Pods have new names; the Deployments are identical.

## One-click design for the card

Four buttons, each showing its result before the next is enabled.

* **Plan** shows the ordered preview as a list (kind, name, namespace, what happens), the Pods that will restart, the
  Jaeger address and the nodes that proved it, the measurements, and every blocker in words. Nothing is changed.
* **Apply** is enabled only for a ready Plan. It opens the native dialog; after approval it shows each stage and its
  gate reading, and stops with the reason if a gate fails.
* **Verify** shows the verdict, the three expected calls with their trace counts, how many traces carry reviews-v2
  evidence, and any drift.
* **Undo** opens its dialog, then reports the snapshot comparison.

The card never offers Apply while the cluster is not Ready, never hides a blocker, and shows an unreadable measurement
as unknown, not as zero.

## Memory and the swapping Mac

Measured: Docker Desktop VM capped at 4 GiB, about 2.0 GiB used, Mac with 2.7 to 3.7 GiB of swap in use. Bookinfo's
four containers use about 441 MB. Istio publishes no istiod memory figure, so the limits are caps, not estimates:
istiod 512 MiB, sidecars 128 MiB each. Worst case at the caps is about 1.28 GiB (istiod 512, four sidecars 512,
reviews-v2 about 115, the temporary Pod about 144) against about 1.9 GiB of headroom. It fits with room to spare,
but the realistic risk on this Mac is a slowdown from more swapping, not a failure. The gates measure real usage at
each stage. "Docker used" is the sum of `docker stats`, a close view of the VM, not an exact one.

## Not verified yet

* Whether a **Pod** reaches the Jaeger receiver (nodes do). Verify exercises this with the client Pod.
* That istiod accepts an external OTLP provider through this `ServiceEntry`. Istio's documentation shows an in-cluster
  collector and does not cover this case.
* Real istiod and sidecar memory; the span names and tags the sidecars emit; and that a trace starts at the client's
  sidecar (expected, not documented).
* `crictl stats` and the `discovery` / `istio-proxy` container names, which the measurements rely on.
* Windows and Linux: swap reading on Linux is implemented but not run there; nothing here has run on Windows.

## Decisions for review

1. Is a failed gate allowed to remove what the run created without asking again (the dialog says so), or should it
   stop and ask?
2. Should Apply pull `istioctl` itself (pinned and checksum-verified), or should the person install it once?
3. Where the snapshot and the "created by this run" record live (proposed: the helper's private config directory).
