# Bookinfo tracing: Apply, Verify, Undo and one-click controls

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One click in the Docker Desktop extension plans, installs, verifies and removes Istio tracing for the Bookinfo benchmark, with every cluster change approved first and every failure rolled back to the recorded baseline.

**Architecture:** A new Go package `internal/traceinstall` holds the only code that can write to a cluster for this feature. It receives a `kube.Client` (the person's own kubeconfig credential, in memory only) and talks to it through `Guarded`, a writer that checks an in-code allow-list before every request. `internal/bookinfotrace` stays read-only and keeps its guard tests. Istio is not installed with `istioctl install`: a pinned, checksum-verified `istioctl` only *renders* the 32 objects offline, and the helper creates exactly those objects itself, so the inventory of what the run owns is exact and Undo deletes that inventory and nothing else. Progress and recovery state live in a durable, permission-restricted record bound to the cluster identity.

**Tech Stack:** Go (helper, stdlib `net/http`, `archive/tar`, `archive/zip`; `go.yaml.in/yaml/v3` already a dependency), Python 3.13 / FastAPI (trace verdict), vanilla JS (extension page).

**Spec:** `docs/BOOKINFO_TRACING_SETUP.md` and the decisions recorded in this conversation on 2026-10-22 (below).

## Global Constraints

- Failed gate or error: automatically roll back **only** what this approved session created. The approval dialog says so. If rollback fails, keep the record and report what remains.
- `istioctl` 1.31.2 only, downloaded from `https://github.com/istio/istio/releases/download/1.31.2/<asset>`, SHA-256 compiled into the helper, stored privately, platforms outside the table are refused.
- Platforms: darwin/arm64, darwin/amd64, linux/amd64, linux/arm64, windows/amd64. Asset names and SHA-256 (published `.sha256` files, 2026-10-22):
  - `istioctl-1.31.2-osx-arm64.tar.gz` `b5f57049a6fe4b8ac39f13a9f28fcdd2a8cbfac9a086be56d9f09f490765960f`
  - `istioctl-1.31.2-osx.tar.gz` `17be5cc9031d4a44a574e3170914a5ea705148b9e8af1c6ee38f065fdb8790ba`
  - `istioctl-1.31.2-linux-amd64.tar.gz` `6ce061447857b54cc90314fd8a72f0f8ed234f67cb0d6e3fb3ef3739caaa5edb`
  - `istioctl-1.31.2-linux-arm64.tar.gz` `62a493e287f2547397a6a7fc7d0fa2df91e041081800c9ca8b6bbfa3a4e23378`
  - `istioctl-1.31.2-win.zip` `784791b0f8127ef6e3ab514da41821d0e0d69849e7847118864e4c44d2c45961`
- Record: per-user config directory, directory 0700, file 0600 (Unix; Windows relies on the per-user profile ACL), atomic write (temp + rename), integrity digest over cluster identity, installation ID, baseline, snapshot and inventory. A record that fails its digest or has loose permissions is refused.
- The extension's read-only credential, RBAC and backend are not touched. No token, kubeconfig content or cluster error text is printed, logged or stored.
- No change to `app/engine/*`, `app/shared/*`, Members 1 to 4. No sample or synthetic traces.
- Nothing is applied to a cluster, no Pod restarted, the running extension not updated and nothing pushed during this work. Tests use in-memory clusters and fake runners only.
- Commit messages carry no attribution trailers.

## Review Focus

- Interrupted Apply (context cancelled, process killed) leaves a journal that Undo can finish; Apply refuses to start over it.
- A cluster recreated under the same context name (different kube-system UID) is refused by Undo and Verify.
- An object with our name but not our installation label or UID is never deleted.
- Existing Istio, foreign objects in `istio-system`, and foreign Istio custom resources are preserved, and the CRDs/namespace are kept with a report.
- Rollback itself failing keeps the record with a `remaining` list and `rollback-failed` phase.
- A swap/Docker memory reading that cannot be taken stops the run, on every OS (Windows reads the page file).
- Corrupt or world-readable record, zip/tar archive entries with path traversal, and a tampered or truncated download are refused.

## Tasks

### Task 1: Raw writes in `internal/kube`
**Files:** modify `kube/kube.go`, `kube/http.go`, `kube/kubetest/fake.go`; test `kube/http_test.go`, `kube/kubetest/fake_test.go`.
**Interfaces:** Produces `Object.UID string`, `(*Object).Raw() map[string]any`, `ObjectFromRaw(map[string]any) (*Object, error)`, and
```go
type RawWriter interface {
    CreateRaw(ctx context.Context, r Resource, namespace string, body map[string]any) (uid string, err error)
    DeleteRaw(ctx context.Context, r Resource, namespace, name, uid string) error // uid precondition; "" = none
    PatchLabels(ctx context.Context, r Resource, namespace, name string, set map[string]*string) error // nil value removes
}
```
`*httpClient` and `*kubetest.Fake` implement `RawWriter`.
- [ ] Tests: create posts the body to the right path and returns `metadata.uid`; delete sends `preconditions.uid`; patch sends `application/merge-patch+json` with `null` for removal; fake records verbs `create`/`delete`/`patch`.
- [ ] Implement; run `go test ./internal/kube/...`.

### Task 2: Embedded manifests, expected inventory, checksum table
**Files:** create `traceinstall/manifests/*.yaml` (copies of `k8s-bookinfo/tracing`, plus `reviews-v2.yaml` extracted unmodified from `bookinfo.yaml`), `traceinstall/data/istio-objects.json` (the 32 rendered objects: apiVersion, kind, namespace, name), `traceinstall/artifacts.go`; modify `bookinfotrace/changeset.json` (recreate order ratings, details, productpage); test `tests/tracing/test_bookinfo_manifests.py` (copies identical to the Bookinfo repo).
**Interfaces:** `Artifacts map[string]Artifact{Asset, SHA256, Kind("tar.gz"|"zip"), Binary}` keyed `"<goos>/<goarch>"`; `ArtifactFor(goos, goarch string) (Artifact, error)`; `ExpectedIstioObjects() []ObjectRef`.
- [ ] Tests: five platforms present, windows/arm64 and freebsd refused; inventory has 32 entries and 15 CRDs; embedded copies equal the Bookinfo repo files.

### Task 3: Pinned `istioctl` manager and offline render
**Files:** create `traceinstall/istioctl.go`; test `istioctl_test.go`.
**Interfaces:** `type Fetcher interface{ Get(ctx, url string, max int64) ([]byte, error) }`, `Ensure(ctx, Env) (bin string, err error)`, `Render(ctx, Env, bin string, installYAML []byte) ([]Manifest, error)`.
- [ ] Tests: host must be `github.com` and path the pinned asset; size cap; wrong hash refused and nothing stored; tar and zip with a single expected entry accepted, traversal/extra entries/symlinks refused; second call reuses the verified binary; a binary altered after verification is refused; render uses a clean environment (`KUBECONFIG` pointing at a missing file) and its result must equal the expected inventory exactly.

### Task 4: Durable record
**Files:** create `traceinstall/store.go`; test `store_test.go`.
**Interfaces:** `Record`, `Store{Dir string}`; `Load(identity) (*Record, error)`, `Save(*Record) error`; phases `applying, applied, rolling-back, rolled-back, rollback-failed`; inventory entry states `intended, created, deleted, foreign`.
- [ ] Tests: 0700/0600 on Unix, atomic replace (a failed write leaves the old file), digest mismatch and loose mode refused, record contains no credential text, record bound to cluster identity (context, server, CA hash, kube-system UID).

### Task 5: Allow-list writer
**Files:** create `traceinstall/guard.go`; test `guard_test.go`.
**Interfaces:** `Guarded` wraps `kube.RawWriter`; `Policy` allows only: Istio objects in the expected inventory, the five bookinfo-namespace objects, reviews-v2, planned Pod deletions, the `istio-injection` label on `bookinfo`, and the client Pod. Creates must carry the managed-by and installation-ID labels.
- [ ] Tests: another namespace, Secrets, RBAC outside the inventory, existing Deployments, other labels, unlabelled creates are all refused before any request is made.

### Task 6: Apply, rollback and Undo engine
**Files:** create `traceinstall/apply.go`, `rollback.go`, `wait.go`, `sim_test.go` (in-memory cluster with failure injection), `apply_test.go`, `rollback_test.go`; modify `bookinfotrace/dialog.go`, `plan.go`.
**Interfaces:** `Apply(ctx, Deps, Params) Outcome`, `Undo(ctx, Deps, Params) Outcome`.
- [ ] Tests: happy path creates exactly the planned set in order and ends `applied`; decline and timeout change nothing; plan drift between approval and first write aborts; fail at every individual step (table-driven) leaves the cluster equal to the snapshot; context cancellation mid-run rolls back; gate failure rolls back; rollback failure keeps the record and `remaining`; Apply over an incomplete record refuses; Undo finishes an interrupted run; foreign objects, foreign custom resources and existing Istio are preserved; UID/label mismatch not deleted; wrong cluster identity refused; single-replica downtime is disclosed in the dialog.

### Task 7: Verify
**Files:** create `traceinstall/verify.go`, `verify_test.go`.
- [ ] Tests: drift reported, uninstrumented Pod fails, client Pod created and always deleted (also on error), unrelated state unchanged.

### Task 8: Commands and cross-OS support
**Files:** modify `main.go`, `flow/*` (export `AdminCluster`, `IdentityOf`), `bookinfotrace/measure.go` (Windows page file); tests `main_bookinfo_test.go`, `bookinfotrace/measure_test.go`.
**Interfaces:** `trace-bookinfo-apply|undo|verify|status <context>`.
- [ ] Tests: usage, JSON-only output, plan unchanged and still read-only, `GOOS=windows|linux|darwin` `go vet` and `go build` pass.

### Task 9: Trace verdict and page controls
**Files:** modify `app/integrations/tracing/trace_path.py`, `extension/build_ui.py`; create `extension/ui-src/bookinfo-tracing.js` and `.test.mjs`; tests `tests/tracing/test_trace_path.py`.
- [ ] Tests: `servicesReporting` lists which Bookinfo services delivered spans; the page shows Plan, Enable tracing, Verify, Undo, enables each only when valid, shows blockers and unknowns in words, polls status during Apply.

### Task 10: Documentation, security review, commits
- [ ] Update `docs/BOOKINFO_TRACING_SETUP.md`; run Python, Go, Node suites; commit locally per task; do not push.
