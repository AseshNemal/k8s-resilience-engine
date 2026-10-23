# Member 4: kind validation runbook (readiness, procedure, recovery)

Owner: Member 4. Companion to `docs/MEMBER4_REAL_VALIDATION_PROTOCOL.md` (what is measured and why). This page says how the first
real validation is to be set up and run, what stops it, and how to undo it.

**Nothing in this runbook has been run.** No cluster has been created, no Kubernetes write has been made, no node has been stopped,
and nothing here is a result. Every command in it is for a person to run, after the gates in section 3 are signed.

## 1. Resource assessment for this machine (measured 2026-10-23)

Measured with the read-only `python -m experiments.member4_real_validation.resource_assessment` (five fixed commands; each container
counted at its largest of three readings):

| | |
| --- | --- |
| Host | arm64, 8.00 GiB |
| Docker VM | 3.82 GiB, 5 CPUs (so about 4.2 GiB is left for macOS) |
| In use by the 6 running containers | about 1.8 GiB (the Docker Desktop cluster: a kind control-plane and two workers, plus Jaeger and two Docker Desktop helpers) |
| VM headroom | about 2.1 GiB |
| Per-node memory taken from the existing kind nodes (a proxy, not a measurement of the new cluster) | control-plane about 0.8 GiB, worker about 0.55 GiB |
| Lower bound for the minimal profile (1 control-plane + 2 workers), **nodes only** | about 1.9 GiB |
| Spare after the nodes, before the application and a 512 MiB margin | about 0.2 GiB |
| **Verdict** | **INSUFFICIENT** (minimal profile); the recommended 3-worker profile is further out of reach |

Two cautions on the figures. The existing cluster's control-plane read 1.10 GiB, then 0.79, then 0.81 GiB within minutes of a Docker
restart, so a single reading proves nothing; the tool therefore takes several and keeps the largest. And the application was **not**
counted at all: Bookinfo's `reviews` runs a Java server and is the heaviest of its four services; its memory must be measured
before it can be budgeted.

**So the test cluster must not be created on this machine as it is configured.** The options below are decisions for the
developer. None has been taken, and this project does not take them (the second two change the state of the Docker Desktop
environment, which is out of scope here):

1. **Raise the Docker Desktop VM's memory** (Settings, Resources). It restarts Docker and the research cluster. Keeping at
   least 2 GiB for the host bounds the VM at 6 GiB on this 8 GiB machine (it is 3.8 GiB now); the host's pageout counter was already high,
   so the practical ceiling is lower, and swapping would distort the experiment's timing.
2. **Run the experiment while the research cluster is not running** (Docker Desktop's Kubernetes switched off by the developer). This frees
   about 1.8 GiB but leaves the research environment down for the duration.
3. **Use a different machine** (a Linux host or VM with more memory). The design is host-independent except for `docker`.

After a decision, re-run the assessment; with the application deployed, re-run it with `--app-memory-mib N` from a measurement.
Only `SUFFICIENT` clears gate G1.

Also observed (read-only): a Docker network named `kind` already exists, so a second kind cluster will attach to it. Inspect it first
(`docker network inspect kind`) for subnet clashes. `kindest/node:v1.36.1` is on the machine; the Bookinfo images are not, and must be
pulled and loaded into the nodes.

## 2. The reproducible environment

All files are in `experiments/member4_real_validation/`.

| File | Purpose |
| --- | --- |
| `kind-validation-cluster.yaml` | The cluster: name `resilience-validation` (never `desktop`), the image already on the machine, the probe port published **on the control-plane container only** (a port on a worker would die with the worker that is killed), loopback only |
| `manifests/probe-nodeport.yaml` | NodePort 30080 for the entry service, so `http://127.0.0.1:18080/productpage` reaches it |
| `designation.example.json` | The form of the designation (a person fills it; the example is deliberately not valid) |
| `resource_assessment.py`, `preflight.py` | Read-only checks |
| `compose.py` | Wires the gateway, injector, probe and executor; read-only unless a `WriteApproval` is given |

Creating it (for a person to run, after G1 to G4):

```bash
mkdir -p "$HOME/.resilience-validation"
kind create cluster --config experiments/member4_real_validation/kind-validation-cluster.yaml \
    --kubeconfig "$HOME/.resilience-validation/kubeconfig"
```

`--kubeconfig` is not optional. Without it kind writes the new cluster into `~/.kube/config` **and makes it the current context**, which
the backend and the installed extension follow. The gateway refuses that file for the same reason.

Then, with that credentials file only:

1. Read the cluster's identity once: the UID of its `kube-system` namespace
   (`kubectl --kubeconfig "$HOME/.resilience-validation/kubeconfig" get namespace kube-system -o jsonpath='{.metadata.uid}'`).
2. Write the designation (outside the repository) from `designation.example.json`: name, API address, that UID, the experiment namespace.
3. Create the namespace and deploy the application **without Istio sidecars**. Istio is not installed in this project's validation. Apply `manifests/probe-nodeport.yaml`.
4. Confirm the probe URL answers from this machine and check that the page really prints the strings the probe treats as a dependency failure
   (the probe uses `Sorry, product details are currently unavailable` and `Sorry, product reviews are currently unavailable` for Bookinfo's product page; **verify these against the deployed image**, they are from memory of the sample and not yet checked here).
5. Run `python -m experiments.member4_real_validation.preflight --designation FILE --kubeconfig FILE --recovery-dir DIR --check-cluster`.

## 3. Readiness gates (all must be true, and recorded, before the first run)

| # | Gate | Who/what | How it is checked |
| --- | --- | --- | --- |
| G1 | The machine can hold the cluster and the application | assessment | `resource_assessment` says `SUFFICIENT` with the measured application memory |
| G2 | A separate disposable kind cluster exists, named for this purpose, with its own credentials file | person | `preflight`: kind cluster, credentials |
| G3 | The designation pins the cluster's `kube-system` UID and a dedicated namespace | person | `preflight`: designation; the executor re-checks identity before every write |
| G4 | The application is deployed, healthy, without Istio; the probe URL answers | person | `preflight --check-cluster`; manual probe |
| G5 | The failure kind is a node stop (`docker kill`), not a drain | code | `FAULT_NOT_A_NODE_FAILURE` refusal; method recorded |
| G6 | A dry run of the read-only gateway against the cluster succeeded | person | `preflight --check-cluster` |
| G7 | The recommendation was computed **against this cluster** (its own backend instance and database) from a workflow **Member 1 froze** *or*, until Member 1 can, from a workflow the developer **declared** and approved through the research tier (`docs/MEMBER4_INPUT_SOURCES.md` section 4: a named acknowledgement, bound to the declaration's digest; the run must be built with `allow_declared_workflow=True`; every record says `workflowBasis: DECLARED` and its results are conditional on the declaration) | person | approval gate; the proposal fingerprint is re-checked at apply |
| G8 | Authorizations exist: `FAIL_BASELINE`, `APPLY`, `FAIL_NODE`, and one `STOP_KIND_WORKER_CONTAINER` per kill (two with a measured baseline) | person | each is bound to the plan digest and the target; a wrong one is refused |
| G9 | A `WriteApproval` names this cluster's UID | person | `compose.build_stack` refuses any other |
| G10 | Pre-registered: number of repetitions, agreement thresholds, the probe pass rule, which baselines are compared | person | written before run 1; recorded with the experiment |
| G11 | The recovery directory exists and holds no pending record | person | `preflight`; the injector refuses to act otherwise |
| G12 | The person starting the run has read section 5 | person | sign-off |

Sign-off (one line each, by hand, with a date): G1 ____ G2 ____ G3 ____ G4 ____ G5 ____ G6 ____ G7 ____ G8 ____ G9 ____ G10 ____ G11 ____ G12 ____

## 4. The run (for a person, after sign-off)

Order, from `compose.py` and `ValidationRun`:

1. `build_stack(config)`: verifies the cluster, captures the baseline. Read-only.
2. Build the plan from the approved recommendation; `arm_stop_authorizations(stack, plan, who, count=2)` (baseline and after the change).
3. `ExperimentRecorder.start(...)`: the prediction is written before anything changes.
4. `recorder.execute(run, id, apply=..., failure=..., baseline=...)`: measured baseline failure (kill, probe, restore); apply; settle; observe the
   placement the scheduler produced; kill, probe for the failure window, restore; revert the Deployments.
5. Read the record: `ExperimentRepository.get(id)`. Predicted, observed, probe and the comparison are separate sections.

What the record will and will not support:

* Placement and readiness are read from the cluster; the **probe** is an independent HTTP observation recorded beside them, with its
  windows. When the probe and readiness disagree both are kept, and the verdict stays the readiness-derived one (a decision for G10, not
  something to settle after seeing results).
* Only a `COMPLETED` run whose `evidence_class` is `REAL_CLUSTER` can come back from `list_measurements()`.
* A single run is one observation, not an accuracy figure.

## 5. Abort and recovery

Stop at once if the run ends `needsManualRestore`, a recovery record is not `RESTORED`, or a node does not return.

1. List what is pending (changes nothing): `python -m app.integrations.kind.recovery DIR`.
2. Restore the stopped workers: `python -m app.integrations.kind.recovery DIR --restore --cluster resilience-validation`, or by hand
   `docker start <container>` (the exact command is in each record).
3. If a Deployment still carries the patch (`workloadsReverted` false): the record's `baselineDeploymentVersions` and
   `proposal.yaml` say what was changed. Because the cluster is disposable, the ultimate recovery is to delete and recreate it:
   `kind delete cluster --name resilience-validation --kubeconfig "$HOME/.resilience-validation/kubeconfig"`.
4. Do not start another run until `preflight` shows no pending record.

Never touch `desktop-*` containers. The injector refuses them by name and by kind's labels, but the person should not be near them either.

## 6. What is implemented and what is not verified against Kubernetes

Implemented and tested with doubles (fake API server, scripted `docker`, in-memory application): the gateway, the injector with durable
recovery, the probe, the executor, the recorder, the composition, the preflight and the assessment, including a full experiment over the
real components with a measured baseline.

**Unverified against a real cluster, real Docker, or a real application:**

* that the gateway's reads and its PATCH bodies are accepted by a real API server (the strategic-merge `null` removals in the restore especially);
* that `docker kill` on a kind worker produces a NotReady node and unready pods in the time the executor waits, and that `docker start` brings
  the node back with its pods;
* that Bookinfo's page prints the strings the probe judges, and that a request through the control-plane port mapping reflects endpoint changes;
* timing: the node-monitor grace period, endpoint propagation, and what the probe sees in the detection window;
* the resource figures for the new cluster and the application;
* that the engine's prediction agrees with any of it.
