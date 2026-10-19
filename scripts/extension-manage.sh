#!/usr/bin/env bash
# Build, install, update, inspect and clean up the Resilience Engine Docker Desktop extension.
#
#   ./scripts/extension-manage.sh install   [--version X.Y.Z]   build the image and install the extension
#   ./scripts/extension-manage.sh update    [--version X.Y.Z]   build a new version and replace the installed one
#   ./scripts/extension-manage.sh uninstall                     remove the extension (asks first)
#   ./scripts/extension-manage.sh status                        installed version, state and local image tags
#   ./scripts/extension-manage.sh check                         run the test suites that guard the extension
#   ./scripts/extension-manage.sh clean                         delete old local image tags (asks first)
#
#   Options: --version X.Y.Z  tag to build (default: the next patch version, never one that already exists)
#            --skip-checks    do not run the test suites before building
#            --dry-run        print every command instead of running it
#            --yes            do not ask for confirmation
#
# This only manages the Docker extension and its image. It never touches your Kubernetes cluster or its
# access objects: approving cluster access happens in the extension's own dialog, and
# scripts/extension-cluster-access.sh --revoke removes it. An update keeps the extension's data volume.
set -euo pipefail

IMAGE="k8s-resilience-engine"
EXTENSION_ID="k8s-resilience-engine"
DOCKERFILE="extension/Dockerfile"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

COMMAND=""
VERSION=""
SKIP_CHECKS="no"
DRY_RUN="no"
ASSUME_YES="no"

# The header comment above is the help text: every comment line after the shebang, up to the first code line.
usage() {
  echo "Usage: scripts/extension-manage.sh <command> [options]"
  echo
  awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "${BASH_SOURCE[0]}"
}
die() { echo "ERROR: $1" >&2; exit "${2:-1}"; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help|help) usage; exit 0 ;;
    --version) VERSION="${2:-}"; [[ -n "$VERSION" ]] || die "--version needs a value such as 0.2.0" 2; shift 2 ;;
    --skip-checks) SKIP_CHECKS="yes"; shift ;;
    --dry-run) DRY_RUN="yes"; shift ;;
    --yes|-y) ASSUME_YES="yes"; shift ;;
    -*) { echo "Unknown option: $1"; echo; usage; } >&2; exit 2 ;;
    *)
      [[ -z "$COMMAND" ]] || die "Only one command at a time (got '$COMMAND' and '$1')." 2
      COMMAND="$1"; shift ;;
  esac
done

if [[ -z "$COMMAND" ]]; then { usage; } >&2; exit 2; fi
case "$COMMAND" in
  install|update|uninstall|status|check|clean) ;;
  *) { echo "Unknown command: $COMMAND"; echo; usage; } >&2; exit 2 ;;
esac

if [[ -n "$VERSION" && ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "--version must look like X.Y.Z (for example 0.2.0), got '$VERSION'." 2
fi

if [[ "$COMMAND" != "check" ]]; then
  command -v docker >/dev/null 2>&1 || die "'docker' is not installed or not on PATH."
fi
cd "$REPO_ROOT"

# Print a command, then run it unless this is a dry run.
run() {
  printf '+ %s\n' "$*"
  if [[ "$DRY_RUN" = "yes" ]]; then return 0; fi
  "$@"
}

# After the real commands: in a dry run nothing happened, so say that instead of reporting success.
dry_run_notice() {
  if [[ "$DRY_RUN" != "yes" ]]; then return 1; fi
  echo
  echo "Dry run: nothing was changed."
}

confirm() {
  if [[ "$ASSUME_YES" = "yes" || "$DRY_RUN" = "yes" ]]; then return 0; fi
  printf 'Proceed? [y/N] '
  read -r answer || answer=""
  case "$answer" in y|Y|yes|YES) return 0 ;; *) echo "Aborted; nothing was changed."; exit 1 ;; esac
}

# ---- reading Docker's state (always real, even in a dry run: these never change anything) -----------

# The JSON object for this extension, or nothing when it is not installed.
our_extension_json() {
  docker extension ls --format json 2>/dev/null | tr '\n' ' ' \
    | sed 's/},[[:space:]]*{"name"/}\
{"name"/g' | grep "\"image\":\"${IMAGE}:" | head -n1 || true
}

installed_version() {
  our_extension_json | sed -n "s/.*\"image\":\"${IMAGE}:\([0-9][0-9.]*\)\".*/\1/p" | head -n1 || true
}

installed_state() {
  our_extension_json | sed -n 's/.*"vm":{[^}]*"status":"\([^"]*\)".*/\1/p' | head -n1 || true
}

# Plain X.Y.Z tags only, oldest to newest. Tags such as 0.1.5-qs belong to someone's experiment: untouched.
local_tags() {
  docker images "$IMAGE" --format '{{.Tag}}' 2>/dev/null | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -u | sort -t. -k1,1n -k2,2n -k3,3n || true
}

highest_of() { grep -v '^$' | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1 || true; }

bump_patch() {
  local major minor patch
  IFS=. read -r major minor patch <<<"$1"
  echo "${major}.${minor}.$((10#$patch + 1))"
}

# The next version nobody has used: above both the installed version and every local tag. Docker keeps
# running the old image when a tag is reused, so a reused tag would look like a successful no-op update.
next_version() {
  local base
  base="$( { installed_version; local_tags; } | highest_of)"
  if [[ -z "$base" ]]; then echo "0.1.0"; else bump_patch "$base"; fi
}

# ---- commands ----------------------------------------------------------------------------------------

cmd_check() {
  local py=".venv/bin/python"
  [[ -x "$py" ]] || py="python3"
  run "$py" -m pytest -q
  run bash -c 'cd extension/helper && go test ./... -count=1'
  run node --test extension/ui-src/*.test.mjs tests/frontend/connection-state.test.mjs tests/frontend/api.test.mjs
}

build_image() {
  if [[ "$SKIP_CHECKS" = "no" ]]; then cmd_check; fi
  run docker build -f "$DOCKERFILE" -t "$IMAGE:$1" .
}

cmd_install() {
  local current
  current="$(installed_version)"
  [[ -z "$current" ]] || die "The extension is already installed (version $current). Use 'update' instead."
  local version="${VERSION:-$(next_version)}"
  build_image "$version"
  run docker extension install -f "$IMAGE:$version"
  if dry_run_notice; then return 0; fi
  echo
  echo "Installed $IMAGE:$version. Open Resilience Engine in Docker Desktop, choose Connect cluster, and"
  echo "use Set up access to approve read-only access to your kind cluster."
}

cmd_update() {
  local current
  current="$(installed_version)"
  [[ -n "$current" ]] || die "The extension is not installed. Use 'install' first."
  local version="${VERSION:-$(next_version)}"
  if [[ "$version" = "$current" ]] || local_tags | grep -qx "$version"; then
    die "Version $version already exists locally. Docker would keep running the old image, so pick a new version."
  fi
  build_image "$version"
  run docker extension update -f "$IMAGE:$version"
  if dry_run_notice; then return 0; fi
  echo
  echo "Updated $current -> $version. Reopen the extension in Docker Desktop. Your data volume was kept."
  echo "If the role in the cluster changed, the Connect cluster page asks you to approve it once."
}

cmd_uninstall() {
  local current
  current="$(installed_version)"
  if [[ -z "$current" ]]; then echo "The extension is not installed; nothing to uninstall."; return 0; fi
  echo "This will remove the Resilience Engine extension (version $current) from Docker Desktop."
  confirm
  run docker extension uninstall "$EXTENSION_ID"
  if dry_run_notice; then return 0; fi
  echo
  echo "Your cluster access was not touched. To remove it from the cluster too, run:"
  echo "  ./scripts/extension-cluster-access.sh --revoke"
}

cmd_status() {
  local current state
  current="$(installed_version)"
  if [[ -n "$current" ]]; then
    state="$(installed_state)"
    echo "Extension : installed, version $current${state:+ ($state)}"
  else
    echo "Extension : not installed"
  fi
  echo "Local image tags for $IMAGE:"
  local tags
  tags="$(local_tags)"
  if [[ -z "$tags" ]]; then echo "  (none)"; else sed 's/^/  /' <<<"$tags"; fi
}

cmd_clean() {
  local keep
  keep="$(installed_version)"
  [[ -n "$keep" ]] || keep="$(local_tags | highest_of)"
  local old=() tag
  while IFS= read -r tag; do
    [[ -n "$tag" && "$tag" != "$keep" ]] && old+=("$tag")
  done < <(local_tags)
  if [[ ${#old[@]} -eq 0 ]]; then echo "Nothing to clean."; return 0; fi
  echo "Keeping $IMAGE:${keep:-none}. These local images will be deleted:"
  for tag in "${old[@]}"; do echo "  $IMAGE:$tag"; done
  confirm
  for tag in "${old[@]}"; do
    run docker image rm "$IMAGE:$tag" || echo "  skipped $IMAGE:$tag (it may be in use)"
  done
}

"cmd_$COMMAND"
