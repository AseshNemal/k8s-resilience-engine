"""The experiments that were run on a cluster, and how well the model predicted them. Pure: reads records, writes nothing.

Every row here is a node failure that actually happened on a cluster and was recorded: a worker stopped by an approved validation run
(``validation-run``) or by the developer while this app watched (``watched-failure``). Nothing is simulated and nothing is carried over
from a bundled recording. A record belongs to the cluster whose identity it stored. Only records of the CONNECTED cluster enter the
figures; records of any other cluster are listed apart (``elsewhere``) and never counted, because a service called "reviews" on
another cluster says nothing about this one. A record that stored no cluster identity cannot be tied to any cluster and is counted
apart as ``unattributed``.

"Accuracy" is only ever a count of experiments where a prediction and a measurement exist for the same failure: how many matched.
With none, it is ``None`` (unknown), never 100% and never 0%.
"""

from typing import Any

PREDICTION_NOTE = ("The model predicts which services lose every ready replica. A match means the services measured down were the "
                   "services predicted down, for the workflow's own services. One failure is one observation, not an accuracy rate.")


def _down_from_readiness(outcome: dict[str, Any]) -> list[str] | None:
    services = ((outcome.get("readiness") or {}).get("services")) or {}
    if not services:
        return None
    return sorted(name for name, entry in services.items() if entry.get("ok") is False)


def _validation_rows(run: dict[str, Any], experiments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per finished experiment of a run. Planned, running and skipped ones never ran, so they are not experiments."""
    before_prediction: dict[str, list[str]] = {}
    for e in experiments:
        predicted = (e.get("outcome") or {}).get("predicted") or {}
        if e["kind"] == "candidate" and "servicesDownBefore" in predicted:
            before_prediction.setdefault(e["worker"], predicted["servicesDownBefore"])
    rows = []
    for e in experiments:
        if e["status"] not in ("PASS", "FAIL", "INCONCLUSIVE"):
            continue
        outcome = e.get("outcome") or {}
        predicted = outcome.get("predicted") or {}
        baseline = e["kind"] == "baseline"
        expected = before_prediction.get(e["worker"]) if baseline else predicted.get("servicesDown")
        scope = set(predicted.get("workflowServices") or [])
        down = _down_from_readiness(outcome)
        if down is not None and scope:
            down = [name for name in down if name in scope]
        match = None if expected is None or down is None else sorted(expected) == down
        readiness = outcome.get("readiness") or {}
        probe = outcome.get("probe") or {}
        rows.append({
            "source": "validation-run", "id": f"{run['runKey']}/{e['kind']}/{e.get('candidateId') or '-'}/{e['worker']}/{e['repeat']}",
            "runKey": run["runKey"], "clusterUid": run.get("clusterUid"), "clusterName": run.get("targetName"), "namespace": run.get("namespace"),
            "at": e.get("finishedAt") or e.get("startedAt") or run.get("finishedAt") or run.get("startedAt"),
            "failedNode": e["worker"], "withChange": not baseline, "candidateId": e.get("candidateId"), "repeat": e["repeat"],
            "status": e["status"], "predictedDown": None if expected is None else sorted(expected), "observedDown": down, "match": match,
            "workflowHeld": readiness.get("holds"), "checkPassed": probe.get("workflowSurvives") if probe else None,
            "recoveryVerified": (outcome.get("recovery") or {}).get("verified"), "durationS": e.get("durationS"),
            "hasTimeline": bool(outcome.get("timeline")), "reasons": list(outcome.get("reasons") or []),
        })
    return rows


def _observation_row(record: dict[str, Any]) -> dict[str, Any] | None:
    failed = record.get("failedNode")
    if not failed:
        return None          # nothing went down while it was watched: there is no failure to compare
    baseline = (record.get("baseline") or {}).get("services") or {}
    least = (record.get("observed") or {}).get("leastReady") or {}
    down = sorted(s for s, n in least.items() if baseline.get(s, 0) > 0 and n == 0)
    predicted = (record.get("predicted") or {}).get(failed)
    comparable = predicted is not None and not record.get("multipleFailures")
    expected = sorted(predicted["servicesDown"]) if comparable else None
    return {
        "source": "watched-failure", "id": record["observationId"], "runKey": None,
        "clusterUid": (record.get("cluster") or {}).get("clusterUid"), "clusterName": None, "namespace": record.get("namespace"),
        "at": record.get("endedAt") or record.get("startedAt"), "failedNode": failed, "withChange": None, "candidateId": None, "repeat": 1,
        "status": record.get("status"), "predictedDown": expected, "observedDown": down,
        "match": None if expected is None else expected == down, "workflowHeld": None, "checkPassed": None,
        "recoveryVerified": record.get("status") == "RECOVERED", "durationS": None, "hasTimeline": False,
        "reasons": [] if comparable else ["More than one worker failed, or this node was not assessed at the start, so it is not compared."],
    }


def build(status: dict[str, Any], runs: list[tuple[dict[str, Any], list[dict[str, Any]]]], observations: list[dict[str, Any]]) -> dict[str, Any]:
    """``status``: the cluster's answer now (``state``, ``clusterUid``). ``runs``: (run, its experiments). ``observations``: stored watches."""
    rows: list[dict[str, Any]] = []
    for run, experiments in runs:
        rows.extend(_validation_rows(run, experiments))
    for record in observations:
        row = _observation_row(record)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: r.get("at") or "", reverse=True)

    connected = status.get("state") == "connected" and bool(status.get("clusterUid"))
    uid = status.get("clusterUid") if connected else None
    mine = [r for r in rows if connected and r["clusterUid"] == uid]
    elsewhere = [r for r in rows if r["clusterUid"] and r["clusterUid"] != uid]
    unattributed = [r for r in rows if not r["clusterUid"]]

    compared = [r for r in mine if r["match"] is not None]
    matched = [r for r in compared if r["match"]]
    by_node: dict[str, dict[str, int]] = {}
    for r in compared:
        entry = by_node.setdefault(r["failedNode"], {"compared": 0, "matched": 0})
        entry["compared"] += 1
        entry["matched"] += 1 if r["match"] else 0
    accuracy = {
        "experimentsCompared": len(compared), "matched": len(matched),
        "agreement": None if not compared else round(len(matched) / len(compared), 4),
        "byNode": [{"node": n, **v} for n, v in sorted(by_node.items())],
        "differences": [r for r in compared if not r["match"]],
        "note": PREDICTION_NOTE,
    }
    return {
        "evidence": "MEASURED", "cluster": {"state": status.get("state"), "clusterUid": status.get("clusterUid"), "context": status.get("context")},
        "connected": connected, "experiments": mine, "accuracy": accuracy if connected else None,
        "elsewhere": {"count": len(elsewhere), "clusters": sorted({r["clusterUid"] for r in elsewhere})},
        "unattributed": {"count": len(unattributed)},
        "counts": {"validationRuns": sum(1 for r in mine if r["source"] == "validation-run"),
                   "watchedFailures": sum(1 for r in mine if r["source"] == "watched-failure"), "recorded": len(rows)},
    }
