# Real data only: Phase 1 (fail closed)

The running application uses live Kubernetes evidence or fails with an explicit error. It never serves
sample, demo or fixture data in its place. This page records the contracts and the known limits.
Later phases (Service/EndpointSlice resolution, wider RBAC, cluster switching) are **not** implemented
and are not claimed.

## Error contract

Every evidence problem is an HTTP error whose body is:

```json
{"detail": {"code": "CLUSTER_NOT_CONNECTED", "message": "...", "component": "deployment_topology",
            "retryable": false, "details": {}}}
```

| Code | HTTP | Meaning |
|---|---|---|
| `CLUSTER_NOT_CONNECTED` | 503 | No usable kubeconfig is available |
| `CLUSTER_CONNECTION_FAILED` | 502 | The API server could not be reached (network, DNS, TLS, timeout) |
| `CLUSTER_AUTH_FAILED` | 502 | The API server rejected the credential (401) |
| `CLUSTER_FORBIDDEN` | 502 | The credential may not perform a required read (403); names verb and resource only |
| `DISCOVERY_FAILED` | 502 | Any other API error; the full exception is in the server log only |
| `INVALID_NAMESPACE` | 422 | Not a valid Kubernetes namespace name |
| `TOPOLOGY_INCOMPLETE` | 409 | A service the workflow depends on has unresolved or missing placement |
| `WORKFLOW_EVIDENCE_UNAVAILABLE` | 404 | No frozen (approved) workflow graph exists |
| `RECOMMENDATION_EVIDENCE_UNAVAILABLE` | 409 | No recommendation has been generated from live evidence |
| `DECLARED_STATE_UNAVAILABLE` | 409 | The view needs declared deployment intent and none exists: no named manifests and no Deployments in the cluster |
| `SNAPSHOT_PROVENANCE_UNVERIFIED` | 409 | A stored snapshot's origin cannot be confirmed against the active connection |
| `APPROVAL_PROVENANCE_UNVERIFIED` | 409 | The approval has no verifiable link to live evidence, so it cannot be approved |
| `REFERENCE_EVIDENCE_UNAVAILABLE` | 409 | No reference graph source is configured, or it has none for the workflow |

Messages never contain credentials, kubeconfig content or raw exception text.

## Discovery envelope (`GET /api/deployments/discovery?namespace=`)

The unchanged `DeploymentGraph` contract (`graph`) wrapped with:

* `status`: `ok` (everything resolved), `empty` (connected, no matching workloads: a valid result) or
  `partial` (some of the namespace could not be resolved: never treat as complete);
* `capturedAt`, `cluster.connectionFingerprint`, `cluster.identityVerified` (always `false`);
* `warnings[]` (`NAMESPACE_EXISTENCE_UNVERIFIED`, `NO_WORKER_NODES`, `WORKLOAD_SCALED_TO_ZERO`);
* `unresolved[]` (`POD_NOT_SCHEDULED`, `POD_ON_EXCLUDED_NODE`, `NO_MATCHING_DEPLOYMENT`,
  `WORKLOAD_HAS_NO_PODS`), each with the services it might affect;
* `serviceEvidence{}`: per service, whether its placement evidence is complete and why not;
* `declaredState`: `available` only when declared intent was found (named manifests, or the cluster's Deployments).

`/api/failure-impacts/runtime` refuses (`TOPOLOGY_INCOMPLETE`) when any service the workflow references
is missing from the topology or has incomplete evidence. Otherwise the prediction proceeds and the
response carries `X-Topology-Status`, `X-Topology-Captured-At` and `X-Topology-Warnings`. A prediction
is a computed result about a virtual failure, not an observation.

## Unknown stays unknown

`ValidationMetrics` accuracy fields and `PodModel.restart_count` are `null` when not measured or not
reported; `0` and `1.0` mean observed or measured values. Unknown zone, CPU and memory are `null`.
Pods without a node are listed as unresolved instead of being placed on an invented node.
Without any declared intent, `constraint_compliance_rate` is `null` and `declared_state` is `"unavailable"`;
`driftDetected` on a service then reflects actual placement only.

## Where declared intent comes from

In this order: a manifest file or folder the caller names; `RESILIENCE_MANIFEST_PATH` (an operator setting
on the backend, never taken from a request, so a client cannot make the server read an arbitrary path); the
repository's own manifests (present only in a checkout); and finally the Deployment specs of the connected
cluster, which the same manifest parser reads because the Kubernetes API returns them in the manifest schema.
The last is what the packaged extension uses. It is the intent as applied to the cluster, which can differ
from the manifests in git, which is why named manifests rank higher.

## What is unavailable on purpose

* Sample, demo, fixture-simulation and Jaeger-seeding routes, and `/api/validations/*`.
  Research versions of the fixture routes exist only in `tests/support/research_routes.py`.
  Member 2's chaos-scenario and benchmark routes remain as labelled dry-runs on scripted placements; they
  are not evidence about the connected cluster.
* Approvals: until a live recommendation pipeline is wired in, none can be requested. Existing records
  are kept; those without the live-evidence marker are shown as `unverified`, can be rejected, and
  cannot be approved.
* Stored snapshots are served only while the connection that produced them is still active and are
  labelled historical.
* Creating a dependency assessment on the Workflows page (it started from a synthetic fixture graph).
* Graph-level accuracy for an approved assessment (`GET /api/workflows/dependency-assessments/{id}/graph-evaluation`):
  it needs an edge-level reference graph. No reviewed one exists in the product (the user-supplied classification
  references hold service labels only), so it answers `REFERENCE_EVIDENCE_UNAVAILABLE` (409). Research code passes
  a `ReferenceSource` explicitly (for example `fixture_repository.FIXTURE_REFERENCE_SOURCE`, which is labelled
  synthetic), and every successful answer reports `referenceProvenance` (source, workflow, `synthetic`).
* The restored Recommendations components render only supplied evidence; see `docs/RECOMMENDATION_LIVE_DATA.md`.

## Known limitations

* Pods are attributed to Deployments by `app` label == Deployment name. Service selectors and
  EndpointSlices are not read, so unattributed pods are reported, not resolved. Do not claim generic
  Kubernetes application discovery.
* StatefulSets, DaemonSets and standalone pods are not modelled.
* The namespace's existence cannot be verified with the current read permissions.
* `connectionFingerprint` (API server address plus CA) detects a changed connection; it is not proof of
  which cluster this is. Frozen workflow graphs are not bound to a cluster.
* The helper still supports only local `kind-*` contexts and its RBAC is unchanged.
* Istio Bookinfo is **not discoverable yet**: its Deployments (`productpage-v1`, `reviews-v2`, ...) do not equal
  their pods' `app` label, so every pod and Deployment is reported as unresolved (`partial`, no services).
  Resolution through Pod → ReplicaSet → Deployment ownership and Service selectors is the next Member 2 work and
  needs read access to `replicasets` and `services`, which the read-only role now grants (EndpointSlices
  are deliberately not read; a Service without a selector is reported as unresolved).
* The Go helper, extension credential flow and `extension/ui-src/*` are untouched.

## Running the tests

```bash
python -m pytest                                   # isolated: no ambient kubeconfig, fake cluster; the benchmark test writes to a temp folder
node --test tests/frontend/*.test.mjs extension/ui-src/*.test.mjs
RESILIENCE_TEST_KUBECONFIG=/path/to/test-kubeconfig python -m pytest -m live_cluster   # opt-in only
```

`tests/conftest.py` points `HOME` and `KUBECONFIG` at an empty sandbox and makes the `kubernetes`
loaders refuse the real `~/.kube`. A test that supplies its own kubeconfig is unaffected.

## Auditing stored approvals (read-only)

```bash
DATABASE_URL=sqlite:////path/to/resilience.db python scripts/report_unverified_approvals.py
```

Nothing is deleted or modified; SQLite files are opened read-only.
