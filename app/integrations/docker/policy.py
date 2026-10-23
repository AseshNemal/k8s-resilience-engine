"""What the Docker Engine proxy will forward, decided from the request alone plus the labels of the container it names.

The Docker socket is root-equivalent power over the Docker VM, so it is mounted into ONE small proxy container and nowhere else.
The backend, which is the process that holds the credentials and runs the research code, reaches Docker only through that proxy,
and the proxy applies this policy to every request. It does not trust the backend's own checks: an endpoint allow-list alone is
not enough, because ``POST /containers/{name}/kill`` is the same request for a kind worker and for any other container. So the
policy also authorizes the CONTAINER:

* a container can be killed or started only when its name is ``<cluster>-worker[N]``, ``<cluster>`` is one of the clusters this
  proxy was configured to allow, AND the daemon reports kind's own labels for it: the same cluster, and the role ``worker``. A
  control-plane container, a container of another application, and a worker of a cluster that was not allowed all fail, whatever
  the name says;
* the Docker Desktop research cluster (``desktop``) is not in the default list. It is allowed only if the person who deploys the
  proxy adds it to its configuration, which is a deliberate act outside the backend;
* the kill signal must be SIGKILL: an abrupt node failure is the only thing this exists to cause;
* inspect is allowed only for such containers, and the proxy trims the answer to the few fields a run needs, because a full
  inspection of another container could carry its environment (secrets);
* everything else, including exec, create, run, stop, rm, pause, copy, volumes, images, networks and the events stream, is refused.

Read-only facts for the memory assessment (``/info``, the container list, one-shot stats of a named container) are allowed with no
options, and the proxy trims each answer to the few fields the assessment uses: a raw ``/info`` or container list describes the
whole host and every container's labels, mounts and ports, none of which a run needs.

Pure: the caller supplies a function that looks up a container's labels.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import parse_qs

KIND_CLUSTER_LABEL = "io.x-k8s.kind.cluster"
KIND_ROLE_LABEL = "io.x-k8s.kind.role"
DEFAULT_ALLOWED_CLUSTERS = frozenset({"resilience-validation"})

_VERSION = re.compile(r"^/v1\.[0-9]{2}(?=/)")
_WORKER = re.compile(r"^(?P<cluster>[a-z0-9][a-z0-9-]{0,40})-worker[0-9]*$")
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,62}$")
_CONTAINER_PATH = re.compile(r"^/containers/(?P<name>[^/]+)/(?P<action>json|kill|start|stats)$")
KILL_SIGNALS = frozenset({"SIGKILL", "KILL", "9"})
READ_ONLY_ACTIONS = {"/_ping": "ping", "/info": "info", "/containers/json": "list"}

Labels = Callable[[str], "dict[str, str] | None"]


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    container: str | None = None
    action: str | None = None


def _deny(reason: str) -> Decision:
    return Decision(False, reason)


def normalize(path: str) -> str:
    """The path without an API version prefix. Anything odd (encoded characters, dot segments, doubled slashes) is left as is,
    and will not match any allowed pattern."""
    return _VERSION.sub("", path or "")


def decide(method: str, path: str, query: str, labels_of: Labels, *, allowed_clusters: frozenset[str] = DEFAULT_ALLOWED_CLUSTERS) -> Decision:
    method = (method or "").upper()
    path = normalize(path)
    arguments = parse_qs(query or "")

    if method == "GET" and path in READ_ONLY_ACTIONS:
        if arguments:
            return _deny("this read-only endpoint takes no options here (no filters, no sizes, no streaming)")
        return Decision(True, "read-only", action=READ_ONLY_ACTIONS[path])

    matched = _CONTAINER_PATH.match(path)
    if matched is None:
        return _deny("this endpoint is not one the validation run needs")
    name, action = matched.group("name"), matched.group("action")
    if not _SAFE_NAME.match(name):
        return _deny("not a plain container name")

    if method == "GET" and action == "stats":
        if arguments.get("stream", ["true"])[0].lower() not in ("false", "0"):
            return _deny("stats may only be read once, not streamed")
        return Decision(True, "read-only", name, action)

    verb_method = {"json": "GET", "kill": "POST", "start": "POST"}[action] if action != "stats" else "GET"
    if method != verb_method:
        return _deny(f"{action} is a {verb_method} request")

    worker = _WORKER.match(name)
    if worker is None or worker.group("cluster") not in allowed_clusters:
        return _deny("only worker containers of an allowed kind cluster can be inspected, stopped or started")
    labels = labels_of(name)
    if labels is None:
        return _deny("the daemon has no such container, or its labels could not be read")
    if labels.get(KIND_CLUSTER_LABEL) != worker.group("cluster") or labels.get(KIND_ROLE_LABEL) != "worker":
        return _deny("the container is not a kind worker of that cluster")

    if action == "kill":
        signals = arguments.get("signal", [])
        if len(signals) != 1 or signals[0] not in KILL_SIGNALS or set(arguments) - {"signal"}:
            return _deny("a node is failed with SIGKILL, and with no other option")
    elif arguments:
        return _deny(f"{action} takes no options here")
    return Decision(True, "authorized worker container", name, action)
