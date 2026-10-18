#!/usr/bin/env bash
# Explicit setup action for the Docker Desktop extension: create a READ-ONLY, time-limited
# credential for the local kind cluster and write it to .kube/extension-readonly.kubeconfig.
# Pick that file in the extension's "Connect cluster" page.
#
#   ./scripts/extension-cluster-access.sh                 create/renew (default token life: 168h)
#   ./scripts/extension-cluster-access.sh --duration 24h  shorter-lived token
#   ./scripts/extension-cluster-access.sh --revoke        remove all access created by this script
#   add --yes to skip the confirmation prompt
#
# This changes the cluster (a namespace, service account, cluster role and binding), so it only
# runs when you invoke it, and it asks first. The app and the extension never run it.
set -euo pipefail

CLUSTER_NAME="${KIND_CLUSTER_NAME:-resilience-research}"
CONTEXT="kind-${CLUSTER_NAME}"
DURATION="168h"
MODE="create"
ASSUME_YES="no"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RBAC_MANIFEST="$REPO_ROOT/deploy/rbac/resilience-readonly.yaml"
KUBE_DIR="$REPO_ROOT/.kube"
OUT_FILE="$KUBE_DIR/extension-readonly.kubeconfig"

die() { echo "ERROR: $*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --duration) DURATION="${2:?--duration needs a value such as 24h}"; shift 2 ;;
    --revoke) MODE="revoke"; shift ;;
    --yes) ASSUME_YES="yes"; shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

command -v kubectl >/dev/null 2>&1 || die "'kubectl' is not installed or not on PATH."
[[ -f "$RBAC_MANIFEST" ]] || die "Missing $RBAC_MANIFEST"

# Always target the kind research cluster explicitly, never whatever context happens to be current.
K=(kubectl --context "$CONTEXT")
"${K[@]}" get --raw=/readyz --request-timeout=10s >/dev/null 2>&1 \
  || die "Cannot reach cluster context '$CONTEXT'. Run ./scripts/cluster-up.sh first."

confirm() {
  [[ "$ASSUME_YES" = "yes" ]] && return 0
  printf 'Proceed? [y/N] '
  read -r answer
  case "$answer" in y|Y|yes|YES) return 0 ;; *) echo "Aborted; nothing was changed."; exit 1 ;; esac
}

if [[ "$MODE" = "revoke" ]]; then
  cat <<EOF
This will DELETE from cluster '$CONTEXT':
  namespace/resilience-engine (and its service account), clusterrole/resilience-engine-reader,
  clusterrolebinding/resilience-engine-reader
All tokens issued for that service account stop working. The local file $OUT_FILE is removed.
EOF
  confirm
  "${K[@]}" delete clusterrolebinding/resilience-engine-reader clusterrole/resilience-engine-reader \
    namespace/resilience-engine --ignore-not-found
  rm -f "$OUT_FILE"
  echo "Access revoked. Disconnect the cluster in the extension's Connect cluster page too."
  exit 0
fi

cat <<EOF
This will CREATE in cluster '$CONTEXT' (see deploy/rbac/resilience-readonly.yaml):
  namespace/resilience-engine, serviceaccount/resilience-engine-reader,
  clusterrole + clusterrolebinding granting ONLY get/list on nodes, pods and deployments.
It then issues a bearer token valid for $DURATION and writes $OUT_FILE (mode 600).
No workloads are deployed and no existing objects are modified.
EOF
confirm

"${K[@]}" apply -f "$RBAC_MANIFEST" >/dev/null
echo "Read-only access objects applied."

# Server address and CA come from the admin context, but the admin credential itself is NOT copied.
server="$("${K[@]}" config view --minify -o jsonpath='{.clusters[0].cluster.server}')"
ca_data="$("${K[@]}" config view --raw --minify -o jsonpath='{.clusters[0].cluster.certificate-authority-data}')"
case "$server" in
  https://127.0.0.1:*|https://localhost:*) api_port="${server##*:}" ;;
  *) die "Unexpected API server address '$server' for context '$CONTEXT'." ;;
esac
[[ -n "$ca_data" ]] || die "No embedded certificate authority found for context '$CONTEXT'."

# The token is held in a shell variable only; it is never printed or passed on a command line.
token="$("${K[@]}" -n resilience-engine create token resilience-engine-reader --duration "$DURATION")"
[[ -n "$token" ]] || die "Token creation failed."

umask 077
mkdir -p "$KUBE_DIR"
chmod 700 "$KUBE_DIR"
tmp_out="$OUT_FILE.tmp"
trap 'rm -f "$tmp_out"' EXIT
# The extension backend runs in the Docker Desktop VM and reaches the API server through the
# port kind publishes on the host. The certificate is verified against the name it lists.
cat >"$tmp_out" <<EOF
apiVersion: v1
kind: Config
clusters:
  - name: kind-readonly
    cluster:
      server: https://host.docker.internal:${api_port}
      tls-server-name: localhost
      certificate-authority-data: ${ca_data}
users:
  - name: resilience-engine-reader
    user:
      token: ${token}
contexts:
  - name: kind-readonly
    context:
      cluster: kind-readonly
      user: resilience-engine-reader
current-context: kind-readonly
EOF
chmod 600 "$tmp_out"
mv "$tmp_out" "$OUT_FILE"
unset token

cat <<EOF

Done. Read-only credential written to .kube/extension-readonly.kubeconfig (valid for $DURATION).

Next: open the extension in Docker Desktop, go to "Connect cluster", and pick that file.

Renew:   re-run this script and pick the new file (older tokens stay valid until they expire).
Revoke:  ./scripts/extension-cluster-access.sh --revoke   (invalidates every token immediately)
Note:    anyone who can read the file can read this cluster's nodes, pods and deployments until
         it expires or is revoked. It cannot change anything or read secrets. Do not commit it.
EOF
