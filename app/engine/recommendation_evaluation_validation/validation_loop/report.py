"""A readable report of one finished test run: one standalone HTML page the person can read, print to PDF, or keep.

Built only from what the run stored: its plan, its approval bounds, every experiment's outcome and measured timeline. It states what was
measured and what was only predicted, in separate places, and it says what it does not show. Nothing is added that the run did not record,
and the page asks for no name: the approval of a run carries none.

Pure: takes the stored run and experiments, returns text. Every value from the record is HTML-escaped; there is no script in the page.
"""

from datetime import datetime, timezone
from html import escape
from typing import Any

from app.engine.recommendation_evaluation_validation.monitoring.summary import compare_episodes, summarize

TONE = {"ok": "#16a34a", "part": "#d97706", "down": "#dc2626", "unk": "#cbd5e1"}
STATUS_WORDS = {"COMPLETED": "Completed", "INTERRUPTED": "Stopped early", "FAILED": "Failed", "NEEDS_MANUAL_RESTORE": "Needs attention",
                "RUNNING": "Still running", "ABORTING": "Ending", "PLANNED": "Planned"}


def e(value: Any) -> str:
    return escape("" if value is None else str(value))


def _when(iso: str | None) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%d %b %Y, %H:%M") if iso else "not recorded"
    except ValueError:
        return "not recorded"


def _secs(value: Any) -> str:
    return "not measured" if value is None else (f"{value:.0f} s" if value >= 10 else f"{value:.1f} s")


def _pct(value: Any) -> str:
    return "not measured" if value is None else f"{round(value * 100)}%"


def _names(values: Any) -> str:
    return "not recorded" if values is None else (", ".join(values) if values else "none")


def _lanes(timeline: dict[str, Any]) -> str:
    """Availability lanes as inline SVG: one row per service, green all replicas, amber some lost, red none, grey not read."""
    samples = timeline.get("samples") or []
    baseline = {k: v for k, v in (timeline.get("baseline") or {}).items() if v}
    if not samples or not baseline:
        return "<p class='note'>No availability samples were recorded.</p>"
    interval = float(timeline.get("intervalS") or 1.0)
    width, left, lane, gap = 640, 110, 14, 4
    t0, t1 = samples[0]["t"], samples[-1]["t"] + interval
    x = lambda t: left + (t - t0) / max(t1 - t0, 1e-6) * (width - left - 8)      # noqa: E731
    rows = []
    for index, (service, before) in enumerate(sorted(baseline.items())):
        y = 18 + index * (lane + gap)
        rows.append(f"<text x='{left - 6}' y='{y + lane - 3}' text-anchor='end' class='lbl'>{e(service)}</text>")
        for i, sample in enumerate(samples):
            ready = (sample.get("ready") or {}).get(service)
            tone = "unk" if ready is None else "ok" if ready >= before else "down" if ready == 0 else "part"
            end = samples[i + 1]["t"] if i + 1 < len(samples) else sample["t"] + interval
            rows.append(f"<rect x='{x(sample['t']):.1f}' y='{y}' width='{max(x(end) - x(sample['t']), 1):.1f}' height='{lane}' fill='{TONE[tone]}'/>")
    marks = []
    names = {"stop": "stopped", "restore": "started again", "node-ready": "Ready", "settled": "back to normal"}
    for mark in timeline.get("marks") or []:
        if mark.get("kind") in names:
            marks.append(f"<line x1='{x(mark['t']):.1f}' x2='{x(mark['t']):.1f}' y1='10' y2='{18 + len(baseline) * (lane + gap)}' stroke='#0f172a' stroke-dasharray='3 3'/>"
                         f"<text x='{x(mark['t']) + 3:.1f}' y='9' class='lbl'>{names[mark['kind']]}</text>")
    height = 28 + len(baseline) * (lane + gap)
    return (f"<svg viewBox='0 0 {width} {height}' class='chart' role='img' aria-label='Availability of each service over time'>"
            + "".join(rows) + "".join(marks) + "</svg>")


def _requests(timeline: dict[str, Any]) -> str:
    samples = [s for s in timeline.get("samples") or [] if s.get("req")]
    if not samples:
        return "<p class='note'>No requests were sent during this test (no page address was set for the workflow check).</p>"
    width, left = 640, 110
    t0, t1 = timeline["samples"][0]["t"], timeline["samples"][-1]["t"] + float(timeline.get("intervalS") or 1.0)
    w = max((width - left - 8) / max(len(timeline["samples"]), 1) - 1, 2)
    cells = "".join(f"<rect x='{left + (s['t'] - t0) / max(t1 - t0, 1e-6) * (width - left - 8):.1f}' y='6' width='{w:.1f}' height='14' "
                    f"fill='{TONE['ok'] if s['req'].get('ok') else TONE['down']}'/>" for s in samples)
    return (f"<svg viewBox='0 0 {width} 28' class='chart' role='img' aria-label='Requests to the application: answered or failed'>"
            f"<text x='{left - 6}' y='17' text-anchor='end' class='lbl'>requests</text>{cells}</svg>")


def _experiment(experiment: dict[str, Any], baselines: dict[str, dict[str, Any]]) -> str:
    outcome = experiment.get("outcome") or {}
    timeline = outcome.get("timeline")
    summary = summarize(timeline)
    predicted = outcome.get("predicted") or {}
    lab = bool(predicted.get("lab"))
    nodes = predicted.get("nodes") or [experiment["worker"]]
    title = ("Workers stopped together: " + ", ".join(nodes)) if lab else (f"{experiment['worker']} stopped"
            + (" — before any change" if experiment["kind"] == "baseline" else " — after the change"))
    recovery = outcome.get("recovery") or {}
    html = [f"<section class='exp'><h3>{e(title)}</h3>"]
    verified = recovery.get("verified")
    html.append(f"<p class='meta'>Result: <b>{e(experiment['status'])}</b> · every stopped worker "
                f"{'was confirmed Ready again' if verified else 'was <b>not</b> confirmed Ready again'}"
                + (f" · held down {_secs(predicted.get('heldS'))} (asked {_secs(predicted.get('holdRequestedS'))})" if lab else "")
                + (f" · took {_secs(experiment.get('durationS'))}" if experiment.get("durationS") else "") + "</p>")
    for reason in outcome.get("reasons") or []:
        html.append(f"<p class='warn'>{e(reason)}</p>")
    if summary:
        down_measured = summary.get("servicesDown")
        req = summary.get("requests")
        html.append("<table class='kv'>")
        html.append(f"<tr><th>Services the placement rule said would lose every ready replica</th><td>{e(_names(predicted.get('servicesDown')) if 'servicesDown' in predicted else 'not recorded')}</td></tr>")
        html.append(f"<tr><th>Services measured with no ready replica</th><td>{e(_names(down_measured))}</td></tr>")
        html.append(f"<tr><th>Requests answered while the workers were down</th><td>{e(_pct(req['windowSuccessRatio']) if req else 'no requests were sent')}"
                    + (f" ({req['windowFailed']} of {req['windowTotal']} failed)" if req else "") + "</td></tr>")
        html.append(f"<tr><th>Longest stretch without an answer</th><td>{e(_secs(req['longestOutageS']) if req else None)}</td></tr>")
        html.append(f"<tr><th>Back to normal after the workers were started</th><td>{e(_secs(summary.get('recoveryAfterRestoreS')))}</td></tr></table>")
        html.append("<h4>Is each service up?</h4>" + _lanes(timeline) + "<p class='legend'><i style='background:#16a34a'></i>all replicas ready "
                    "<i style='background:#d97706'></i>some lost <i style='background:#dc2626'></i>none ready <i style='background:#cbd5e1'></i>not read</p>")
        html.append("<h4>Did the application answer?</h4>" + _requests(timeline))
        resources = summary.get("resources") or {}
        if resources:
            html.append("<h4>Peak resource use per node</h4><table><tr><th>Node</th><th>CPU (millicores)</th><th>Memory (MiB)</th></tr>"
                        + "".join(f"<tr><td>{e(n)}</td><td>{e(v['cpuPeakMillicores'] if v['cpuPeakMillicores'] is not None else 'not read')}</td>"
                                  f"<td>{e(v['memoryPeakMiB'] if v['memoryPeakMiB'] is not None else 'not read')}</td></tr>" for n, v in sorted(resources.items()))
                        + "</table><p class='note'>A node that could not answer (for example while stopped) is shown as not read, never as zero.</p>")
        events = (timeline or {}).get("events") or []
        if events:
            html.append("<h4>What the cluster reported</h4><ul>" + "".join(f"<li><b>{e(ev.get('reason'))}</b> {e(ev.get('object'))} — {e(ev.get('message'))}</li>" for ev in events[:12]) + "</ul>")
        if experiment["kind"] == "candidate" and baselines.get(experiment["worker"]):
            cmp = compare_episodes(baselines[experiment["worker"]], summary)
            html.append("<h4>Before and after the change (same worker)</h4><table><tr><th>Measured</th><th>Before</th><th>After</th><th></th></tr>"
                        + "".join(f"<tr><td>{e(r['label'])}</td><td>{e(r['before'])}</td><td>{e(r['after'])}</td><td>{e(r['verdict'])}</td></tr>" for r in cmp["rows"]) + "</table>")
    else:
        html.append("<p class='note'>No measured timeline was recorded for this experiment.</p>")
    html.append("</section>")
    return "".join(html)


def build_report(run: dict[str, Any], experiments: list[dict[str, Any]], *, now: datetime | None = None) -> str:
    plan, approval, result = run.get("plan") or {}, run.get("approval") or {}, run.get("result") or {}
    mode = plan.get("mode") or {}
    lab = mode.get("mode") == "LAB"
    finished = [x for x in experiments if x["status"] in ("PASS", "FAIL", "INCONCLUSIVE")]
    baselines = {}
    for x in finished:
        timeline = (x.get("outcome") or {}).get("timeline")
        if x["kind"] == "baseline" and timeline and not lab:
            baselines[x["worker"]] = summarize(timeline)
    recovery = run.get("recovery") or {}
    needs_attention = run["status"] == "NEEDS_MANUAL_RESTORE" or (recovery and not recovery.get("complete", True))
    kind = "Failure lab" if lab else f"Validation ({e(str(mode.get('mode') or '').title())})"
    body = [f"<header><h1>Failure test report</h1><p class='meta'>{kind} · {e(STATUS_WORDS.get(run['status'], run['status']))} · "
            f"{e(_when(run.get('startedAt') or run.get('createdAt')))}</p></header>"]
    if needs_attention:
        body.append("<p class='alert'><b>Something could not be restored.</b> Check the cluster: start any stopped worker "
                    "(<code>docker start &lt;worker&gt;</code>) and confirm every node is Ready before another test.</p>")
    body.append("<section><h2>What was tested</h2><table class='kv'>"
                f"<tr><th>Cluster</th><td>{e(run.get('targetName'))} ({e(run.get('targetKind'))}) · identity {e((run.get('clusterUid') or 'unknown')[:12])}</td></tr>"
                f"<tr><th>Application namespace</th><td>{e(run.get('namespace'))}</td></tr>"
                + (f"<tr><th>Workers stopped</th><td>{e(', '.join(mode.get('workers') or []))} — together, held {e(_secs(mode.get('holdS')))}</td></tr>"
                   if lab else f"<tr><th>Workers</th><td>{e(', '.join(mode.get('workers') or []))}</td></tr>"
                   f"<tr><th>Changes tried</th><td>{e(len(plan.get('candidates') or []))}</td></tr>")
                + f"<tr><th>Node failures used</th><td>{e(run.get('killsUsed'))} of at most {e(approval.get('maxFailures'))} approved</td></tr>"
                f"<tr><th>Approval</th><td>One approval of this exact plan (digest {e((approval.get('planDigest') or '')[:12])}); it carries no name.</td></tr></table>")
    if lab and (plan.get("lab") or {}).get("impact"):
        impact = plan["lab"]["impact"]
        body.append("<h3>What the placement rule said before the test</h3><table><tr><th>Service</th><th>Ready replicas</th><th>On the stopped workers</th><th>If they stop</th></tr>"
                    + "".join(f"<tr><td>{e(s['service'])}</td><td>{e(s['ready'])}</td><td>{e(s['lost'])}</td><td>{e({'down': 'goes offline', 'degraded': 'keeps ' + str(s['left']) + ' of ' + str(s['ready']), 'ok': 'unaffected', 'none': 'no ready replica today'}[s['state']])}</td></tr>"
                              for s in impact["services"]) + "</table>")
    body.append("</section>")
    if result.get("winner") or (not lab and result):
        body.append(f"<section><h2>Outcome</h2><p>{'A change passed every planned experiment: ' + e(result.get('winner')) if result.get('winner') else 'No change passed every planned experiment.'}"
                    + (f" {e(result.get('stopReason'))}" if result.get("stopReason") else "") + "</p></section>")
    elif run.get("stopReason"):
        body.append(f"<section><h2>How it ended</h2><p>{e(run['stopReason'])}</p></section>")
    body.append("<section><h2>What happened</h2>" + ("".join(_experiment(x, baselines) for x in finished) if finished else "<p class='note'>No experiment ran to a result.</p>") + "</section>")
    body.append("<section><h2>How to read this</h2><ul>"
                "<li><b>Measured:</b> ready replicas, node status, requests, CPU/memory and events were read from the cluster while the workers were stopped.</li>"
                "<li><b>Predicted:</b> the placement rule says which services lose every ready replica from where their replicas ran. It is a statement about placement, not about your workflow.</li>"
                "<li>Ready replicas are what Kubernetes reports; a pod on a stopped node can stay Ready for a short while. The requests show what a user would have seen.</li>"
                "<li>One test is one observation, not an accuracy rate. Repeat it before relying on it.</li></ul></section>")
    stamp = _when((now or datetime.now(timezone.utc)).isoformat())
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>"
            f"<title>Failure test report · {e(run.get('runKey'))}</title><style>{CSS}</style></head><body><main>"
            + "".join(body) + f"<footer>Generated {e(stamp)} from the stored record of run {e(run.get('runKey'))}. K8s Resilience Engine.</footer></main></body></html>")


CSS = """
body{font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#0f172a;background:#f1f5f9;margin:0}
main{max-width:860px;margin:0 auto;padding:24px 16px 48px;background:#fff}
h1{font-size:26px;margin:0}h2{font-size:18px;border-bottom:1px solid #e2e8f0;padding-bottom:4px;margin-top:28px}h3{font-size:15px;margin:16px 0 4px}h4{font-size:13px;margin:14px 0 4px;color:#475569}
.meta{color:#64748b;margin:4px 0 12px}.note,.legend{font-size:12px;color:#64748b}.warn{color:#b45309}.alert{background:#fee2e2;color:#991b1b;padding:10px 12px;border-radius:8px}
table{border-collapse:collapse;width:100%;margin:6px 0}th,td{border:1px solid #e2e8f0;padding:5px 8px;text-align:left;vertical-align:top}th{background:#f8fafc;font-weight:600}
.kv th{width:46%}.exp{border:1px solid #e2e8f0;border-radius:10px;padding:4px 14px 12px;margin:12px 0;break-inside:avoid}
.chart{width:100%;height:auto}.lbl{font-size:9px;fill:#475569}.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin:0 4px 0 10px}
footer{margin-top:32px;font-size:12px;color:#94a3b8}code{background:#f1f5f9;padding:1px 4px;border-radius:4px}
@media print{body{background:#fff}main{max-width:none;padding:0}}
"""
