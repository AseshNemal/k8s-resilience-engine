"""Watch a worker-node failure the developer causes themselves, and compare what happens with what was predicted.

Nothing here writes to the cluster or stops a node: the failure is the developer's own act, on a cluster they chose. This
service reads the application as Member 3's overview reads it, at the start and on every status request, and records:

* the prediction, fixed at the start, before anything fails (one entry per worker node);
* the observation: the first node seen going NotReady, the fewest ready replicas each service had meanwhile, and when the
  node came back;
* the comparison, only for a single failed node and only for what was observed (service readiness). Workflow outcomes are
  never observed here, so they are reported as not observed and never as survived.

Asking for the status advances the persisted record (the same pattern as the applied-change ledger); nothing runs in the
background, so a stretch with nobody asking is a gap in the record, which the record says.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.engine.recommendation_evaluation_validation.monitoring import watch
from app.engine.recommendation_evaluation_validation.monitoring.sampler import Readers
from app.engine.recommendation_evaluation_validation.monitoring.summary import summarize
from app.engine.recommendation_evaluation_validation.monitoring.timeline import compact
from app.engine.recommendation_evaluation_validation.observation.repository import (
    ObservationAlreadyActive, ObservationRepository,
)
from app.shared.evidence_errors import EvidenceError

WATCHING = "WATCHING"        # no watched node has failed yet
NODE_DOWN = "NODE_DOWN"      # a node that was Ready at the start is not Ready now
RECOVERED = "RECOVERED"      # that node is Ready again: the observation is complete
STOPPED = "STOPPED"          # the developer ended it
EXPIRED = "EXPIRED"          # nothing finished it within the time allowed
ACTIVE = frozenset({WATCHING, NODE_DOWN})

EXPIRES_AFTER = timedelta(hours=2)
MAX_TIMELINE = 200
SHOWN_SAMPLES = 360          # the newest samples a read returns (about 30 minutes of checks); older ones stay stored


class ObservationRefused(EvidenceError):
    def __init__(self, code: str, message: str, *, http_status: int = 409) -> None:
        super().__init__(code, message, http_status=http_status, component="recommendation")


def _iso(moment: datetime) -> str:
    return moment.isoformat()


def _predicted(snapshot: dict[str, Any]) -> dict[str, Any]:
    """What the model said for each worker node, as it stands before any failure."""
    out = {}
    for node in snapshot.get("nodes") or []:
        rows = {row["service"]: row for row in node.get("services") or []}
        out[node["node"]] = {
            "servicesDown": list(node.get("servicesDown") or []),
            "servicesDegraded": list(node.get("servicesDegraded") or []),
            "readyAfter": {name: row.get("readyAfter") for name, row in rows.items()},
            "workflowsLost": node.get("workflowsLost"),
            "workflows": [{"workflowId": w.get("workflowId"), "survivesPredicted": w.get("survives")}
                          for w in node.get("workflows") or []],
        }
    return out


def _ready_services(snapshot: dict[str, Any]) -> dict[str, int]:
    return {row["service"]: int(row.get("readyReplicas") or 0) for row in snapshot.get("services") or []}


def _ready_nodes(snapshot: dict[str, Any], states: dict[str, dict[str, Any]]) -> dict[str, bool | None]:
    """Readiness of the assessed workers, from the node states read on their own: the overview's own flag is unknown on the
    discovery path, and unknown is not "Ready"."""
    return {node["node"]: (states.get(node["node"]) or {}).get("ready") for node in snapshot.get("nodes") or []}


def compare(record: dict[str, Any]) -> dict[str, Any]:
    """Predicted against observed for the node that failed. A pure function of the record."""
    failed = record.get("failedNode")
    if not failed:
        return {"state": "NO_FAILURE_SEEN", "message": "No watched worker node has stopped being Ready yet."}
    if record.get("multipleFailures"):
        return {"state": "NOT_COMPARABLE", "node": failed,
                "message": "More than one worker was seen failing. The prediction covers one node at a time, so it is not compared."}
    predicted = (record.get("predicted") or {}).get(failed)
    if predicted is None:
        return {"state": "NOT_ASSESSED", "node": failed,
                "message": "This node was not one of the workers assessed at the start, so there is no prediction to compare."}
    baseline = record.get("baseline", {}).get("services") or {}
    least = (record.get("observed") or {}).get("leastReady") or {}
    lost = sorted(s for s, n in least.items() if baseline.get(s, 0) > 0 and n == 0)
    degraded = sorted(s for s, n in least.items() if 0 < n < baseline.get(s, 0))
    predicted_down = sorted(predicted["servicesDown"])
    row = {
        "node": failed,
        "predictedServicesDown": predicted_down, "observedServicesDown": lost,
        "predictedServicesDegraded": sorted(predicted["servicesDegraded"]), "observedServicesDegraded": degraded,
        "predictedButStillReady": sorted(set(predicted_down) - set(lost)),
        "downButNotPredicted": sorted(set(lost) - set(predicted_down)),
        "workflows": {"observed": False, "predictedLost": predicted["workflowsLost"],
                      "message": "Workflow outcomes were not observed, only service readiness. A workflow is not reported as "
                                 "having survived or failed here."},
    }
    row["servicesMatch"] = lost == predicted_down
    row["state"] = ("PROVISIONAL" if record.get("status") == NODE_DOWN
                    else "MATCHED" if row["servicesMatch"] else "DIFFERENT")
    row["message"] = {
        "PROVISIONAL": "The node is still down; this is what has been seen so far.",
        "MATCHED": "The services that lost every ready replica are the ones the model predicted.",
        "DIFFERENT": "The services that lost every ready replica differ from the prediction.",
    }[row["state"]]
    return row


class FailureObservationService:
    def __init__(self, engine: Any, overview: Callable[[str | None], dict[str, Any]],
                 node_states: Callable[[], dict[str, dict[str, Any]]], cluster_uid: Callable[[], str | None], *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), expires_after: timedelta = EXPIRES_AFTER,
                 monitor_readers: Callable[[str | None, str | None], Readers | None] | None = None) -> None:
        """``monitor_readers(namespace, request_url)``: the optional extra readings of a check (CPU and memory, warning events, pod
        logs, a real request). Without it the timeline still records availability and node state, which every check reads anyway."""
        self.repo = ObservationRepository(engine)
        self._monitor_readers = monitor_readers
        self._overview, self._node_states, self._cluster_uid = overview, node_states, cluster_uid
        self._clock, self._expires_after = clock, expires_after

    # -- lifecycle ---------------------------------------------------------------------------------------------------

    def start(self, namespace: str | None, started_by: str | None = None, note: str | None = None,
              request_url: str | None = None) -> dict[str, Any]:
        """Fix the prediction and the starting state, then watch. One watch at a time."""
        if self.repo.active() is not None:
            raise ObservationRefused("OBSERVATION_ACTIVE", "A failure is already being watched. Stop it before starting another.")
        uid = self._cluster_uid()                     # EvidenceError propagates: no cluster read, no observation
        if not uid:
            raise ObservationRefused("CLUSTER_IDENTITY_UNKNOWN", "The connected cluster could not be identified, so a watch cannot be "
                                     "tied to it. Nothing was recorded.", http_status=503)
        snapshot = self._overview(namespace)
        baseline_nodes = _ready_nodes(snapshot, self._node_states())
        watched = sorted(name for name, ready in baseline_nodes.items() if ready is True)
        if not watched:
            raise ObservationRefused("NO_READY_WORKER", "No worker node is Ready now, so there is nothing to watch fail.", http_status=409)
        started = self._clock()
        record = {
            "evidence": "developer-initiated failure, observed", "injectedByThisApp": False,
            "cluster": {"clusterUid": uid}, "clusterMatch": "same",
            "startedBy": (started_by or "").strip() or None, "note": (note or "").strip() or None, "startedAt": _iso(started),
            "predicted": _predicted(snapshot), "predictionScope": snapshot.get("scope"),
            "baseline": {"services": _ready_services(snapshot), "nodes": baseline_nodes},
            "requestUrl": (request_url or "").strip() or None,
            "monitor": watch.new_timeline(started, _ready_services(snapshot)),
            "watched": watched, "failedNode": None, "multipleFailures": False, "observed": {"leastReady": {}},
            "timeline": [{"at": _iso(started), "kind": "started", "detail": f"Watching {', '.join(watched)}."}],
            "reads": {"ok": 1, "failed": 0, "lastError": None, "lastReadAt": _iso(started)}, "endedAt": None,
        }
        observation_id = f"obs-{uuid.uuid4().hex[:16]}"
        try:
            self.repo.create(observation_id, namespace or snapshot.get("namespace") or "", record, WATCHING)
        except ObservationAlreadyActive as error:
            raise ObservationRefused("OBSERVATION_ACTIVE", "A failure is already being watched. Stop it before starting another.") from error
        return self.read(observation_id)

    def stop(self, observation_id: str) -> dict[str, Any]:
        row = self.repo.get(observation_id)
        if row is None:
            raise ObservationRefused("OBSERVATION_NOT_FOUND", "No such observation.", http_status=404)
        if row["status"] in ACTIVE:
            self._finish(row, STOPPED, "stopped", "Ended by the developer.")
        return self.read(observation_id)

    def read(self, observation_id: str | None = None) -> dict[str, Any] | None:
        """The observation (default: the watching one, else the latest), advanced from the cluster if still watching."""
        row = self.repo.get(observation_id) if observation_id else (self.repo.active() or next(iter(self.repo.history(limit=1)), None))
        if row is None:
            return None
        if row["status"] in ACTIVE:
            row = self._advance(row)
        return self._public(row)

    def history(self, limit: int = 10) -> list[dict[str, Any]]:
        return [self._public(row, samples=False) for row in self.repo.history(limit=limit)]

    # -- advancing ---------------------------------------------------------------------------------------------------

    def _advance(self, row: dict[str, Any]) -> dict[str, Any]:
        now = self._clock()
        if now - datetime.fromisoformat(row["startedAt"]) > self._expires_after:
            return self._finish(row, EXPIRED, "expired", "Nothing finished this observation in time.")
        # The watch belongs to the cluster it started on. A different or unidentifiable cluster is never read as this one: the same
        # worker and service names on another cluster would otherwise look like this cluster failing or recovering.
        try:
            uid = self._cluster_uid()
        except Exception:
            uid = None
        stored = (row.get("cluster") or {}).get("clusterUid")
        if not uid or not stored:
            return self._unreadable(row, "CLUSTER_IDENTITY_UNKNOWN", "unknown")
        if uid != stored:
            return self._unreadable(row, "CLUSTER_CHANGED", "other")
        try:
            snapshot = self._overview(row["namespace"] or None)
            states = self._node_states()
        except Exception as error:      # unreadable is unknown, never "down" and never "recovered"
            return self._unreadable(row, getattr(error, "code", type(error).__name__), "same")
        reads = {**row["reads"], "ok": row["reads"]["ok"] + 1, "lastError": None, "lastReadAt": _iso(now)}
        timeline = list(row["timeline"])
        marks: list[tuple[str, str]] = []
        status, failed, multiple = row["status"], row["failedNode"], row["multipleFailures"]
        least = dict(row["observed"]["leastReady"])
        nodes, services = _ready_nodes(snapshot, states), _ready_services(snapshot)

        down = [name for name in row["watched"] if nodes.get(name) is False]
        if status == WATCHING and down:
            failed, status = down[0], NODE_DOWN
            timeline.append({"at": _iso(now), "kind": "node-not-ready", "node": failed, "detail": f"{failed} is no longer Ready."})
            marks.append(("node-not-ready", failed))
            multiple = len(down) > 1
        elif status == NODE_DOWN:
            if len([n for n in down if n != failed]) and not multiple:
                multiple = True
                timeline.append({"at": _iso(now), "kind": "second-node-not-ready",
                                 "detail": "Another worker also stopped being Ready; the single-node comparison no longer applies."})
            if nodes.get(failed) is True:
                status = RECOVERED
                timeline.append({"at": _iso(now), "kind": "node-ready", "node": failed, "detail": f"{failed} is Ready again."})
                marks.append(("node-ready", failed))
        if status in (NODE_DOWN, RECOVERED) and row["status"] != RECOVERED:
            for service, ready in services.items():          # the fewest ready replicas seen while the node was down
                before = least.get(service)
                if before is None or ready < before:
                    least[service] = ready
                    if row["baseline"]["services"].get(service, 0) > 0 and ready < row["baseline"]["services"][service]:
                        timeline.append({"at": _iso(now), "kind": "service-replicas-lower", "service": service,
                                         "detail": f"{service}: {ready} of {row['baseline']['services'][service]} replicas ready."})
        started = datetime.fromisoformat(row["startedAt"])
        monitor = self._measure(row, started, now, services, nodes, marks)
        if status == RECOVERED:
            monitor = watch.finish(monitor, started=started, now=now)
        changes = {"monitor": monitor, "reads": reads, "clusterMatch": "same", "failedNode": failed, "multipleFailures": multiple, "observed": {"leastReady": least},
                   "timeline": timeline[-MAX_TIMELINE:]}
        if status == RECOVERED:
            changes["endedAt"] = _iso(now)
        return self._save(row, status, changes)

    def _measure(self, row: dict[str, Any], started: datetime, now: datetime, ready: dict[str, int] | None,
                 nodes: dict[str, bool | None] | None, marks: list[tuple[str, str]]) -> dict[str, Any] | None:
        """One more sample on the watch's timeline. The timeline is extra evidence: if recording it fails, the watch itself goes on."""
        try:
            readers = None
            if ready is not None and self._monitor_readers is not None:
                try:
                    readers = self._monitor_readers(row["namespace"] or None, row.get("requestUrl"))
                except Exception:      # noqa: BLE001  (no extra readers this time: availability and nodes are still recorded)
                    readers = None
            return watch.record(row.get("monitor"), started=started, now=now, ready=ready, nodes=nodes, readers=readers,
                                since_iso=_iso(started), marks=marks)
        except Exception:      # noqa: BLE001
            return row.get("monitor")

    def _unreadable(self, row: dict[str, Any], code: str, match: str) -> dict[str, Any]:
        reads = {**row["reads"], "failed": row["reads"]["failed"] + 1, "lastError": code}
        # The cluster could not be read at this check: that moment goes on the timeline as unknown, never as healthy.
        monitor = self._measure(row, datetime.fromisoformat(row["startedAt"]), self._clock(), None, None, [])
        return self._save(row, row["status"], {"reads": reads, "clusterMatch": match, "monitor": monitor})

    def _finish(self, row: dict[str, Any], status: str, kind: str, detail: str) -> dict[str, Any]:
        now = self._clock()
        timeline = [*row["timeline"], {"at": _iso(now), "kind": kind, "detail": detail}][-MAX_TIMELINE:]
        monitor = watch.finish(row.get("monitor"), started=datetime.fromisoformat(row["startedAt"]), now=now)
        return self._save(row, status, {"timeline": timeline, "endedAt": _iso(now), "monitor": monitor})

    def _save(self, row: dict[str, Any], status: str, changes: dict[str, Any]) -> dict[str, Any]:
        keep = {k: v for k, v in row.items() if k not in ("observationId", "status", "namespace", "createdAt", "updatedAt")}
        payload = {**keep, **changes}
        self.repo.save(row["observationId"], status=status, payload=payload, ended=status not in ACTIVE)
        return {**payload, "observationId": row["observationId"], "status": status, "namespace": row["namespace"],
                "createdAt": row["createdAt"]}

    @staticmethod
    def _public(row: dict[str, Any], *, samples: bool = True) -> dict[str, Any]:
        """The record for the page. ``samples=False`` (the history list) keeps the measured summary and leaves the samples out."""
        stored = row.get("monitor")
        monitor = None
        if stored and stored.get("samples"):
            monitor = {"evidence": "MEASURED", "summary": summarize(stored), "timeline": compact(stored, last=SHOWN_SAMPLES) if samples else None,
                       "checkEveryS": watch.INTERVAL_S, "requests": bool(row.get("requestUrl"))}
        return {**{k: v for k, v in row.items() if k != "monitor"}, "monitor": monitor, "comparison": compare(row), "active": row["status"] in ACTIVE,
                "limits": ["This app stopped nothing: the failure, if any, was caused by the developer.",
                           "Service readiness is read from the cluster when asked. Between two reads a state can be missed, "
                           "and a stopped node's pods can stay Ready until Kubernetes marks them NotReady.",
                           "Workflow outcomes are not observed here."]}
