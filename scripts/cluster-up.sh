#!/usr/bin/env bash
# Create (or reuse) the local kind research cluster and write a dedicated kubeconfig
# for the FastAPI container. Run before `docker compose up --build -d`, and again after
# Docker Desktop restarts.
#
# - Creates the cluster only if it does not exist; an existing cluster and its
#   workloads are preserved (stopped node containers are started, never recreated).
# - Succeeds only if the cluster's API server is actually reachable.
# - Does not deploy workloads, run experiments, or change anything in the cluster.
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-resilience-research}"
READY_TIMEOUT="${CLUSTER_READY_TIMEOUT:-120}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KIND_CONFIG="$REPO_ROOT/deploy/kind/research-cluster.yaml"
KUBE_DIR="$REPO_ROOT/.kube"
APP_KUBECONFIG="$KUBE_DIR/kind-app.config"

die() { echo "ERROR: $*" >&2; exit 1; }

TMP_FILES=()
cleanup() { [[ ${#TMP_FILES[@]} -eq 0 ]] || rm -f "${TMP_FILES[@]}"; }
trap cleanup EXIT

# --- Prerequisites -----------------------------------------------------------
for tool in docker kind kubectl; do
  command -v "$tool" >/dev/null 2>&1 || die "'$tool' is not installed or not on PATH (see docs/DOCKER.md)."
done
docker info >/dev/null 2>&1 || die "Cannot reach the Docker daemon. Start Docker Desktop and wait until it is running."
[[ -f "$KIND_CONFIG" ]] || die "Missing kind config: $KIND_CONFIG"

# --- Create the cluster, or reuse the existing one ---------------------------
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER_NAME"; then
  echo "Kind cluster '$CLUSTER_NAME' already exists; preserving it."
  # Node containers stop when Docker Desktop restarts. Start them again (data is kept).
  while read -r node; do
    [[ -n "$node" ]] || continue
    if [[ "$(docker inspect -f '{{.State.Running}}' "$node")" != "true" ]]; then
      echo "Starting stopped node container: $node"
      docker start "$node" >/dev/null
    fi
  done < <(kind get nodes --name "$CLUSTER_NAME")
else
  echo "Creating kind cluster '$CLUSTER_NAME' (1 control-plane + 3 workers)..."
  kind create cluster --name "$CLUSTER_NAME" --config "$KIND_CONFIG"
fi

# --- Gate: the API server must answer before we report success ---------------
HOST_KUBECONFIG="$(mktemp)"
TMP_FILES+=("$HOST_KUBECONFIG")
echo "Waiting up to ${READY_TIMEOUT}s for the Kubernetes API server..."
deadline=$((SECONDS + READY_TIMEOUT))
until kind get kubeconfig --name "$CLUSTER_NAME" >"$HOST_KUBECONFIG" 2>/dev/null \
      && KUBECONFIG="$HOST_KUBECONFIG" kubectl get --raw=/readyz --request-timeout=5s >/dev/null 2>&1; do
  if [[ "$SECONDS" -ge "$deadline" ]]; then
    die "Cluster '$CLUSTER_NAME' exists but its API server is not reachable after ${READY_TIMEOUT}s.
  Check:  docker ps -a --filter name=$CLUSTER_NAME
  Logs:   docker logs ${CLUSTER_NAME}-control-plane
  The cluster was left untouched. If it is unrecoverable and holds nothing you need, you may
  delete it yourself with 'kind delete cluster --name $CLUSTER_NAME' and re-run this script."
  fi
  sleep 3
done

echo "API server is reachable:"
KUBECONFIG="$HOST_KUBECONFIG" kubectl get nodes
node_count="$(KUBECONFIG="$HOST_KUBECONFIG" kubectl get nodes --no-headers | wc -l | tr -d ' ')"
ready_count="$(KUBECONFIG="$HOST_KUBECONFIG" kubectl get nodes --no-headers | awk '$2=="Ready"' | wc -l | tr -d ' ')"
if [[ "$node_count" != "4" ]] || [[ "$ready_count" != "$node_count" ]]; then
  echo "WARNING: expected 4 Ready nodes (1 control-plane + 3 workers); found $node_count, $ready_count Ready." >&2
fi

# --- Dedicated kubeconfig for the app container -----------------------------
# The app container is intentionally NOT attached to the 'kind' Docker network, so cluster
# nodes and pods cannot resolve it by name. This does not isolate the unauthenticated app API
# (see "Security notes" in docs/DOCKER.md).
# It reaches the API server through the port kind publishes on the host instead:
# https://host.docker.internal:<port>. The API server certificate does not list that name, so
# TLS is verified against the name it does list (tls-server-name=localhost). Certificate
# verification stays on; the cluster CA is embedded in the file.
# Requires Docker Desktop, where host.docker.internal reaches host loopback ports.
server="$(KUBECONFIG="$HOST_KUBECONFIG" kubectl config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
cluster_entry="$(KUBECONFIG="$HOST_KUBECONFIG" kubectl config view --minify -o jsonpath='{.clusters[0].name}')"
case "$server" in
  https://127.0.0.1:*|https://localhost:*) api_port="${server##*:}" ;;
  *) die "Unexpected API server address '$server' in the kind kubeconfig." ;;
esac

umask 077
mkdir -p "$KUBE_DIR"
chmod 700 "$KUBE_DIR"
tmp_out="$APP_KUBECONFIG.tmp"
TMP_FILES+=("$tmp_out")
KUBECONFIG="$HOST_KUBECONFIG" kubectl config view --raw --minify --flatten >"$tmp_out"
KUBECONFIG="$tmp_out" kubectl config set-cluster "$cluster_entry" \
  --server="https://host.docker.internal:${api_port}" --tls-server-name=localhost >/dev/null
chmod 600 "$tmp_out"
mv "$tmp_out" "$APP_KUBECONFIG"

cat <<EOF

Cluster '$CLUSTER_NAME' is ready.
Kubeconfig for the app container: .kube/kind-app.config (mode 600, git-ignored)

Next:  docker compose up --build -d      (if the app is already running: docker compose restart app)

Notes:
- That kubeconfig grants cluster-admin on this local test cluster. Mounting it read-only
  does not restrict what the credentials can do. Do not share or commit it.
- kind worker nodes are containers on one Docker host: logical failure domains, not
  independent physical machines.
EOF
