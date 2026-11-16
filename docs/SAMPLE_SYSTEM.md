# Sample systems: download and use

The research cluster (`kind-resilience-research`) is empty after `scripts/cluster-up.sh`; that script
never deploys workloads. To have something to assess, two sample systems are used:

| Sample | Purpose | Where it comes from |
|---|---|---|
| **Istio Bookinfo** (`bookinfo` namespace) | A real multi-service app with a dependency chain: `productpage → details` and `productpage → reviews → ratings`. One replica each. | Downloaded from the Istio repository (below) |
| **paymentservice** (`resilience-test` namespace) | A deliberately bad deployment: 3 replicas pinned to one worker, so one node failure removes the whole service. | This repository: `deploy/test-workloads/` |

These are *workloads in the cluster*. They are not the `sample_data/*.json` fixtures, which are used by
automated tests only and are never served by the running app (see "Real data only at runtime" in
`CLAUDE.md`).

> Status: the steps below were run on macOS (Docker Desktop 4.95.0, Apple silicon). The apply commands
> were also checked with `kubectl apply --dry-run=server` against the live cluster. Bookinfo is **not yet
> discoverable** by the app; see [What the app shows today](#what-the-app-shows-today).

## Prerequisites

- Docker Desktop, `kind`, `kubectl` and `git` on your `PATH` (macOS: `brew install kind kubectl`).
- The research cluster running: `./scripts/cluster-up.sh` (creates it, or restarts stopped nodes with data kept).
- Internet access on first use. The cluster pulls the images from `registry.istio.io`. No source build is
  needed to run Bookinfo.

**Always pass `--context kind-resilience-research`.** If Docker Desktop's own Kubernetes is enabled, your
current context may be `docker-desktop`, and an unqualified command would deploy there instead.

## 1. Download Bookinfo

This fetches only the `samples/bookinfo` folder (about 1 MB), not the whole Istio repository. Download into
its own folder outside this repository.

```bash
mkdir -p ~/Desktop/SLIIT/k8s-bookinfo && cd ~/Desktop/SLIIT/k8s-bookinfo
git clone --depth 1 --filter=blob:none --sparse https://github.com/istio/istio.git istio-source
git -C istio-source sparse-checkout set samples/bookinfo     # a separate step: the clone alone checks out only root files
cp -Rn istio-source/samples/bookinfo/. .                      # -n never overwrites an existing file
rm -rf istio-source                                           # optional: the temporary clone is no longer needed
```

You now have, directly in `k8s-bookinfo/`:

| Path | Contents |
|---|---|
| `src/productpage`, `src/details`, `src/ratings`, `src/reviews` | Application source (Python, Ruby, Node.js, Java/Gradle) and one `Dockerfile` each |
| `src/build-services.sh`, `src/docker-bake.hcl` | Build all images (only needed if you change the app) |
| `platform/kube/` | Kubernetes manifests, including `bookinfo.yaml` |
| `networking/`, `gateway-api/`, `policy/` | Istio traffic-routing examples. **Not used by this project** (it reads Services and Pods only) |

The downloaded code is upstream, untrusted input: read it before building or running anything from it.
Applying `bookinfo.yaml` needs no build, because it references Istio's prebuilt images.

## 2. Deploy Bookinfo

`bookinfo.yaml` defines three `reviews` versions. The research setup runs **`reviews-v2` only**, which gives
one replica per service (4 pods). The ServiceAccounts carry only an `account=` label, so they are applied
separately.

```bash
cd ~/Desktop/SLIIT/k8s-bookinfo
k() { kubectl --context kind-resilience-research -n bookinfo "$@"; }   # works in bash and zsh

kubectl --context kind-resilience-research create namespace bookinfo
k apply -f bookinfo.yaml -l account                          # all four ServiceAccounts
k apply -f bookinfo.yaml -l 'app in (productpage,details,ratings)'
k apply -f bookinfo.yaml -l 'service=reviews'                # the reviews Service
k apply -f bookinfo.yaml -l 'app=reviews,version=v2'         # the reviews-v2 Deployment

k rollout status deploy --timeout=180s
k get pods -o wide                                           # shows which worker each pod landed on
```

To deploy all three reviews versions (6 pods) instead, run only
`k apply -f bookinfo.yaml` after the namespace exists.

Expected result: `details-v1`, `productpage-v1`, `ratings-v1` and `reviews-v2`, each `1/1 Running`, spread by
the Kubernetes scheduler across `resilience-research-worker`, `-worker2` and `-worker3`.

### Check that it works

```bash
k port-forward svc/productpage 9080:9080
# then open http://localhost:9080/productpage
```

### Remove it

```bash
kubectl --context kind-resilience-research delete namespace bookinfo
```

This deletes only the `bookinfo` namespace and everything in it.

## 3. Deploy the concentrated paymentservice

```bash
kubectl --context kind-resilience-research apply -f deploy/test-workloads/concentrated-paymentservice.yaml
kubectl --context kind-resilience-research apply -f deploy/test-workloads/resilience-probe.yaml
kubectl --context kind-resilience-research -n resilience-test get pods -o wide
```

All three `paymentservice` replicas use a `nodeSelector` so they run on one worker. That is the point of the
sample: three healthy-looking replicas with no real redundancy. `resilience-probe` is a helper pod.

## 4. Use it in the app

1. Open **Resilience Engine** in Docker Desktop and go to **Connect cluster**.
2. Press **Find clusters**, choose `kind-resilience-research`, and press **Connect** (approve the system dialog).
   `docker-desktop` is listed but greyed out: automatic setup supports `kind-*` contexts only. See
   [`EXTENSION.md`](EXTENSION.md).
3. Open **Deployment topology** to see the nodes, pods and services read from the cluster.

For the standalone app (`uvicorn app.main:app`), run `kubectl config use-context kind-resilience-research`
first. The backend reads `$KUBECONFIG`, else `~/.kube/config`, and uses the current context.

### What the app shows today

- **paymentservice** names its Deployment, label and Service the same, so it fits the current resolver, which
  attributes a pod to a Deployment when the `app` label equals the Deployment name.
- **Bookinfo resolves completely.** Its Deployments are `productpage-v1`, `reviews-v2`, ... while its pods are labelled `app: productpage`,
  `app: reviews`, ...; Pod → ReplicaSet → Deployment ownership and Service selectors resolve this (see `CLAUDE.md`, "Current State").
  It has not been checked against every Bookinfo variant.
- No sample or substituted data is ever shown. If the cluster cannot be read, pages report an explicit error
  such as `CLUSTER_NOT_CONNECTED`.

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Pods `Pending` or `ErrImagePull` | The nodes cannot reach `registry.istio.io`. Check network access; do not disable TLS verification if a proxy re-signs traffic. |
| Pods fail with `serviceaccount "bookinfo-..." not found` | The ServiceAccounts were skipped. Run `k apply -f bookinfo.yaml -l account`. |
| Deployed to the wrong cluster | A command lacked `--context kind-resilience-research`. Check with `kubectl config current-context`. |
| Pods restarted after Docker Desktop restarted | The kind node containers stop with Docker Desktop (exit 137). Run `./scripts/cluster-up.sh`; data is kept. |
| `docker-desktop` cannot be selected in the extension | Intended. Only `kind-*` contexts are supported for automatic setup. |
