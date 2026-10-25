/* Apply and test: only an explicit click starts work; polls only read the durable run. */
(function () {
    "use strict";
    const BASE = "/api/recommendations/apply-tests";
    const TERMINAL = new Set(["COMPLETED", "COMPLETED_WITH_GAPS", "TESTS_FAILED", "FAILED", "INTERRUPTED"]);
    const LABELS = { QUEUED: "Queued", RUNNING: "Running", COMPLETED: "Report complete", COMPLETED_WITH_GAPS: "Report complete — missing measurements",
        TESTS_FAILED: "Checks failed", FAILED: "Run stopped", INTERRUPTED: "Run interrupted" };
    const STAGES = [["baseline", "Baseline"], ["applying", "Apply"], ["rollout", "Rollout"], ["testing", "Workflow tests"], ["analysis", "Risk analysis"], ["report", "Report"]];
    const outcomeLabel = (value) => value?.riskTargetMet === false || value?.report?.riskTargetMet === false
        ? "Risk target not met" : LABELS[value?.status] || value?.status;

    const fmtCpu = (raw) => {
        if (raw === null || raw === undefined) return null;
        const match = String(raw).match(/^([0-9]+(?:\.[0-9]+)?)(n|u|m)?$/);
        if (!match) return String(raw);
        const milli = Number(match[1]) * ({ n: 0.000001, u: 0.001, m: 1 }[match[2]] ?? 1000);
        if (milli > 0 && milli < 0.001) return "<0.001 mCPU";
        return `${Number(milli.toFixed(3))} mCPU`;
    };
    const fmtMemory = (raw) => {
        if (raw === null || raw === undefined) return null;
        const match = String(raw).match(/^([0-9]+(?:\.[0-9]+)?)(Ki|Mi|Gi|Ti|k|M|G)?$/);
        if (!match) return String(raw);
        const bytes = Number(match[1]) * ({ Ki: 1024, Mi: 1048576, Gi: 1073741824, Ti: 1099511627776, k: 1000, M: 1000000, G: 1000000000 }[match[2]] ?? 1);
        return `${Number((bytes / 1048576).toFixed(2))} MiB`;
    };

    function createPanel(env) {
        const { root, document, fetchImpl } = env;
        let run = null, pending = false, error = null, timer = null, destroyed = false, epoch = 0, resetting = false;
        let resetFailure = null;      // {message, match}: a refused Reset stays shown until the cluster it was refused on is no longer the one connected
        let live = null;      // the cluster's own picture of the target Deployments and pods; only read while a run is busy
        let expectedRequest = env.pendingRequestKey?.() || null;
        let startError = null;
        let awaitingReset = false;
        const make = (tag, text, cls) => {
            const node = document.createElement(tag);
            if (text !== null && text !== undefined) node.textContent = String(text);
            if (cls) node.className = cls;
            return node;
        };
        const notify = () => { if (env.onChange) env.onChange(); };
        const busy = () => pending || resetting || awaitingReset || run?.application?.status === "RESETTING" || !!(run && !TERMINAL.has(run.status));

        async function request(url, init) {
            const response = await fetchImpl(url, init);
            let body;
            try { body = await response.json(); }
            catch (_error) { throw Object.assign(new Error(`The backend returned an unreadable response (HTTP ${response.status}).`), { status: response.status }); }
            if (!response.ok) throw Object.assign(new Error(body.detail?.message || (typeof body.detail === "string" ? body.detail : `The request failed (HTTP ${response.status}).`)), { status: response.status });
            return body;
        }

        function table(headers, rows) {
            const wrap = make("div", null, "table-scroll");
            const node = make("table", null, "data-table");
            const head = make("thead"), tr = make("tr"), body = make("tbody");
            headers.forEach((h) => { const th = make("th", h); th.setAttribute("scope", "col"); tr.append(th); });
            head.append(tr);
            rows.forEach((row) => { const line = make("tr"); row.forEach((value) => line.append(make("td", value ?? "Unavailable"))); body.append(line); });
            node.append(head, body); wrap.append(node); return wrap;
        }

        function resources(snapshot) {
            const data = snapshot?.resources;
            if (!data || !(data.nodes?.length || data.pods?.length)) return make("p", "CPU/memory usage unavailable. Resource requests are not measured usage.", "ui-note");
            const rows = [];
            (data.nodes || []).forEach((n) => rows.push([`Node ${n.name}`, fmtCpu(n.usage?.cpu), fmtMemory(n.usage?.memory), n.timestamp]));
            (data.pods || []).forEach((p) => (p.containers || []).forEach((c) => rows.push([`${p.name} / ${c.name}`, fmtCpu(c.usage?.cpu), fmtMemory(c.usage?.memory), p.timestamp])));
            const wrap = make("div");
            if (!data.available) wrap.append(make("p", "Some resource measurements are unavailable; the values below are the samples that could be read.", "ui-note"));
            wrap.append(make("p", `Source: ${data.source}. CPU in millicores (1000 mCPU = 1 core); memory in MiB. Raw samples are preserved in the download.`, "ui-note"),
                table(["Resource", "CPU usage", "Memory usage", "Sample time"], rows));
            return wrap;
        }

        function drawReport(report) {
            const details = make("details", null, "apply-test__report");
            details.open = run?.application?.status !== "RESET";
            const partial = !report.after;
            const stopped = partial && TERMINAL.has(run?.status);
            details.append(make("summary", partial ? stopped ? "Partial report — post-apply tests not completed" : "Results collected so far" : "Full report"));
            if (partial) details.append(make("p", stopped
                ? "Only the evidence collected before the run stopped is available. Missing after-results do not indicate a passing test."
                : "The run is still collecting results. Post-apply tests and measurements have not completed.", "ui-note"));
            const analysis = report.riskAnalysis;
            details.append(make("h4", "Risk analysis"), make("p", report.riskMeaning, "ui-note"),
                table(["Before", "Predicted", "After rollout"], [[
                    Number.isFinite(report.baselineRisk?.blastRadius) ? `${report.baselineRisk.blastRadius}%` : null,
                    Number.isFinite(report.predictedRisk) ? `${report.predictedRisk}%` : null,
                    Number.isFinite(analysis?.observed?.blastRadius) ? `${analysis.observed.blastRadius}%` : null]]));
            if (report.riskTargetMet === false) {
                const mismatch = make("p", `The expected protection was not achieved: predicted model risk ${report.predictedRisk}%, after-rollout model risk ${analysis.observed.blastRadius}%. Readiness alone does not establish resilience.`, "approval-note apply-test__mismatch");
                mismatch.setAttribute("role", "alert"); details.append(mismatch);
            }
            if (analysis?.error) details.append(make("p", analysis.error.message, "approval-note"));
            details.append(make("h4", "Workflow working status"));
            const before = new Map((report.before?.workflows?.rows || []).map((r) => [r.workflowId, r]));
            const after = report.after?.workflows?.rows || [];
            const ids = new Set([...before.keys(), ...after.map((r) => r.workflowId)]);
            details.append(table(["Workflow", "Readiness before → after", "HTTP check before → after", "Details"], [...ids].map((id) => {
                const b = before.get(id), a = after.find((r) => r.workflowId === id);
                return [id, `${b?.readiness || "Unavailable"} → ${a?.readiness || "Unavailable"}`,
                    `${b?.httpStatus || "Not tested"} → ${a?.httpStatus || "Not tested"}`,
                    a?.reason || b?.reason || (a?.unreadyServices?.length ? a.unreadyServices.join(", ") : "See response samples in the downloaded report.")];
            })));
            details.append(make("p", "Pod readiness is separate from end-to-end HTTP results. No node failure was injected.", "ui-note"));
            details.append(make("h4", "Resource usage before"), resources(report.before), make("h4", "Resource usage after"),
                report.after ? resources(report.after) : make("p", stopped ? "Not collected: the run stopped before post-apply measurements." : "Waiting for post-apply measurements.", "ui-note"));
            if (report.error) details.append(make("p", report.error.message, "approval-note"));
            const download = make("button", "Download full report (JSON)", "secondary-button");
            download.type = "button";
            download.addEventListener("click", async () => {
                try {
                    const body = await request(`${BASE}/${encodeURIComponent(run.runId)}/report`);
                    if (env.download) env.download(body, `${run.runId}.json`);
                } catch (e) { error = e.message; draw(); }
            });
            details.append(download);
            return details;
        }

        const clock = (iso) => { const d = iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString() : "unknown time"; };

        function drawLive() {
            const section = make("section", null, "apply-test__live");
            section.setAttribute("aria-label", "Live activity");
            section.append(make("h4", "Live activity"));
            const recent = (run?.events || []).slice(-5).reverse();
            if (recent.length) {
                const list = make("ol", null, "apply-test__recent");
                recent.forEach((event, index) => list.append(make("li", `${clock(event.at)} · ${event.message}`, index === 0 ? "is-latest" : "")));
                section.append(list);
            }
            if (!live) { section.append(make("p", "Reading what the cluster is doing…", "ui-note")); return section; }
            if (!live.available) { section.append(make("p", live.message || "The live view is not available yet.", "ui-note")); return section; }
            section.append(make("p", `Updated ${clock(live.capturedAt)} from the cluster · namespace ${live.namespace || ""}`, "ui-note"));
            (live.deployments || []).forEach((deployment) => {
                const head = make("h5", deployment.missing ? `${deployment.name} — not found in the cluster`
                    : `${deployment.name} — ${deployment.ready}/${deployment.desired} ready · ${deployment.updated}/${deployment.desired} on the new version`);
                section.append(head);
                if (deployment.blockedReason) section.append(make("p", deployment.blockedReason, "approval-note"));
                if (deployment.pods?.length) {
                    const versions = { new: "New version", old: "Old version" };
                    section.append(table(["Pod", "Node", "Version", "State"], deployment.pods.map((pod) =>
                        [pod.name, pod.node || "Not scheduled yet", versions[pod.version] || "Unknown", pod.restarts ? `${pod.state} (${pod.restarts} restarts)` : pod.state])));
                } else if (!deployment.missing) section.append(make("p", "No pods yet.", "ui-note"));
            });
            if (live.nodes?.length) {
                const health = (n) => n.ready === false ? "Not ready" : n.schedulable === false ? "Cordoned: takes no new pods" : n.ready === true ? "Ready" : "Unknown";
                section.append(make("h5", "Nodes"), table(["Node", "Pods from this run", "Status", "CPU", "Memory"],
                    live.nodes.map((n) => [n.name, n.pods, health(n), fmtCpu(n.cpu) ?? "Unavailable", fmtMemory(n.memory) ?? "Unavailable"])));
            }
            return section;
        }

        function draw() {
            root.replaceChildren();
            root.hidden = !run && !pending && !error;
            root.setAttribute("aria-busy", String(busy()));
            if (root.hidden) return;
            root.append(make("h3", pending ? "Starting Apply and tests…" : run ? outcomeLabel(run) : "Run status unavailable"));
            if (run) root.append(make("p", `${run.candidateId || "Selected change"} · ${run.namespace || ""} · ${run.clusterName || "connected cluster"}`, "ui-note"));
            if (!env.readOnly && run?.application?.status === "RESET") root.append(make("p", "The saved configuration is restored. Select another candidate below. This report remains in Test history.", "ui-note"));
            if (run || pending) {
                const stages = make("ol", null, "apply-test__stages");
                stages.setAttribute("aria-label", "Apply and test stages");
                const phase = run?.current?.phase;
                STAGES.forEach(([key, label]) => {
                    const reached = (run?.events || []).some((event) => event.phase === key);
                    const item = make("li", label);
                    item.setAttribute("data-state", key === phase ? "current" : reached ? "reached" : "pending");
                    if (key === phase) item.setAttribute("aria-current", "step");
                    stages.append(item);
                });
                root.append(stages);
            }
            if (busy()) { const progress = make("progress"); progress.setAttribute("aria-label", "Apply and test run in progress"); root.append(progress); }
            if (run?.current) {
                const operation = make("section", null, "apply-test__current");
                operation.append(make("h4", "Current operation"), make("p", run.current.message), make("code", run.current.command, "apply-test__command"));
                root.append(operation);
            }
            if (busy() && run) root.append(drawLive());
            if (error) { const note = make("p", error, "approval-note"); note.setAttribute("role", "alert"); root.append(note); }
            if (awaitingReset || run?.application?.status === "RESETTING") root.append(make("p", "Restoring the saved configuration. Your test report stays saved. Candidate selection will return when restoration completes.", "ui-note"));
            const events = make("ol", null, "apply-test__events");
            (run?.events || []).forEach((event) => {
                const item = make("li"); item.append(make("span", `${event.at} · ${event.phase}: ${event.message}`), make("code", event.command)); events.append(item);
            });
            (run?.application?.events || []).forEach((event) => {
                const item = make("li", `${event.at} · ${event.event}: ${event.detail}`); events.append(item);
            });
            if ((run?.events?.length || 0) + (run?.application?.events?.length || 0)) {
                const history = make("details", null, "apply-test__history");
                history.append(make("summary", "Operation history"), events);
                root.append(history);
            }
            if (run?.report && (run.report.before || TERMINAL.has(run.status))) root.append(drawReport(run.report));
            if (!env.readOnly && run?.canReset && !busy()) {
                root.append(make("p", "To test another candidate independently, reset this change first. Its results remain in Test history.", "ui-note"));
                const reset = make("button", "Reset changes", "secondary-button"); reset.type = "button";
                reset.addEventListener("click", resetChanges); root.append(reset);
            }
            const refresh = make("button", "Refresh run status", "secondary-button"); refresh.type = "button";
            refresh.addEventListener("click", poll); root.append(refresh);
        }

        function arm() {
            clearTimeout(timer);
            if (!destroyed && (busy() || error)) timer = setTimeout(poll, 1500);
        }

        async function poll() {
            const generation = epoch;
            try {
                if (!env.readOnly && run && (awaitingReset || run.application?.status === "RESETTING")) {
                    const base = run.declared ? "/api/recommendations/declared" : "/api/recommendations";
                    await request(`${base}/${encodeURIComponent(run.candidateId)}/application`);
                }
                const requestedId = run?.runId || env.runId;
                const value = await request(requestedId ? `${BASE}/${encodeURIComponent(requestedId)}` : `${BASE}/current`);
                if (generation !== epoch || destroyed) return;
                const found = value.runId ? value : value.run || null;
                if (expectedRequest && found?.requestKey !== expectedRequest) {
                    error = startError || "This Apply request has not been confirmed yet. An older report is not its result. Status checks never repeat Apply.";
                    draw(); notify(); arm(); return;
                }
                run = found;
                pending = false; error = run ? null : startError;
                if (resetFailure) {
                    if (run && run.clusterMatch !== resetFailure.match) resetFailure = null;
                    else error = resetFailure.message;
                }
                if (!env.readOnly && run?.application?.status === "RESETTING") awaitingReset = true;
                if (awaitingReset && run?.application?.status === "RESET") {
                    awaitingReset = false;
                    env.onReadyForNext?.();
                }
                if (run?.application?.status === "RESET_FAILED") {
                    awaitingReset = false;
                    error = "Restoration failed. Inspect the saved application before testing another candidate.";
                }
                if (run && TERMINAL.has(run.status)) { expectedRequest = null; env.clearRequestKey?.(); }
                live = null;
                if (run?.runId && busy()) {
                    try { const view = await request(`${BASE}/${encodeURIComponent(run.runId)}/live`); if (generation === epoch && !destroyed) live = view; }
                    catch (_e) { live = null; }      // the live view is extra; the run's own status above is what counts
                }
            } catch (e) { if (generation === epoch) error = e.message; }
            draw(); notify(); arm();
        }

        async function apply(selection) {
            if (env.readOnly || busy()) return;
            epoch += 1; pending = true; error = null; startError = null; resetFailure = null; run = null; draw(); notify();
            // Scrolling is optional presentation; a host webview failure must not prevent the request.
            try { root.scrollIntoView?.({ block: "start", behavior: "smooth" }); } catch (_error) { /* Keep Apply working. */ }
            expectedRequest = selection.requestKey;
            arm(); // Read persisted progress even if the POST response is slow or lost.
            const generation = epoch;
            try {
                const result = await request(BASE, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(selection) });
                if (generation !== epoch || destroyed) return;
                run = result; pending = false;
            } catch (e) {
                pending = false;
                if (e.status && e.status < 500) { expectedRequest = null; env.clearRequestKey?.(); }
                startError = `${e.message} Apply is not repeated automatically.`;
                error = startError;
            }
            draw(); notify(); arm();
        }

        async function resetChanges() {
            if (env.readOnly || busy() || !run?.canReset) return;
            resetting = true; error = null; resetFailure = null; draw(); notify();
            try {
                const base = run.declared ? "/api/recommendations/declared" : "/api/recommendations";
                await request(`${base}/${encodeURIComponent(run.candidateId)}/reset`, { method: "POST", headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ resetBy: "Explicit Reset action" }) });
                awaitingReset = true;
                if (env.onReset) env.onReset();
            } catch (e) {
                error = e.message; resetFailure = { message: e.message, match: run?.clusterMatch }; resetting = false;
                draw(); notify(); arm(); return;       // arm: re-check, so the message goes once the connected cluster is no longer the one it was about
            }
            resetting = false; await poll();
        }

        draw();
        return { start: poll, apply, reset: resetChanges,
            // Active means Apply was clicked for this candidate and the change is still on the cluster (running, finished but not reset, or being restored).
            state: () => ({ run, busy: busy(), error, resetting: resetting || awaitingReset || run?.application?.status === "RESETTING",
                active: !!run?.candidateId && (!!run.canReset || !TERMINAL.has(run.status) || ["RESETTING", "RESET_FAILED"].includes(run.application?.status)) }),
            destroy() { destroyed = true; clearTimeout(timer); },
            clusterChanged() { epoch += 1; run = null; pending = false; error = null; startError = null; awaitingReset = false; expectedRequest = null; clearTimeout(timer); draw(); return poll(); } };
    }
    // ---- comparing saved tests: what to show, decided apart from how it is drawn --------------------------------------------------------
    const RISK_MATCH_TOLERANCE = 1;          // percentage points; a measured risk further than this from the predicted one is flagged
    const TYPE_SLUGS = { "combination": "COMBINATION", "topology-spread": "TOPOLOGY_SPREAD", "replica-increase": "REPLICA_INCREASE", "pod-anti-affinity": "POD_ANTI_AFFINITY" };
    const plainStrategy = (type) => { const words = String(type || "").toLowerCase().replace(/_/g, " "); return words.charAt(0).toUpperCase() + words.slice(1); };
    const finite = (value) => typeof value === "number" && Number.isFinite(value);

    /** The first `keep` characters of an identifier; the whole value stays available as a title and for copying. */
    function shortId(value, keep = 8) {
        const text = value === null || value === undefined ? "" : String(value);
        if (!text) return null;
        const bare = text.replace(/^apply-test-/, "");
        return bare.length > keep + 1 ? `${bare.slice(0, keep)}…` : bare;
    }

    /** "Today 05:31", "Yesterday 11:40" or "24 Oct 11:40" for a saved timestamp (stored as UTC without a zone mark); the raw value is the title. */
    function friendlyTime(iso, options = {}) {
        if (!iso) return null;
        const raw = String(iso);
        const moment = new Date(/(Z|[+-]\d\d:?\d\d)$/.test(raw) ? raw : `${raw}Z`);
        if (Number.isNaN(moment.getTime())) return raw;
        const zone = options.timeZone ? { timeZone: options.timeZone } : {};
        const day = (date) => date.toLocaleDateString("en-CA", zone);
        const now = options.now ? new Date(options.now) : new Date();
        const clock = moment.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", ...zone });
        if (day(moment) === day(now)) return `Today ${clock}`;
        if (day(moment) === day(new Date(now.getTime() - 86400000))) return `Yesterday ${clock}`;
        return `${moment.toLocaleDateString("en-GB", { day: "numeric", month: "short", ...zone })} ${clock}`;
    }

    /** A column title a person can read: the strategy and the services, with the raw id only as a fallback or a hover. */
    function candidateTitle(row, strategyName = plainStrategy) {
        let type = row.candidate && row.candidate.type, service = row.candidate && row.candidate.service;
        if (!type || !service) {
            const parsed = /^rec-.*?-([\w+.]+)-(combination|topology-spread|replica-increase|pod-anti-affinity)$/.exec(row.candidateId || "");
            if (parsed) { service = service || parsed[1]; type = type || TYPE_SLUGS[parsed[2]]; }
        }
        if (!type || !service) return { strategy: null, services: row.candidateId || "Unknown candidate", id: row.candidateId };
        return { strategy: strategyName(type), services: String(service).split("+").join(" + "), id: row.candidateId };
    }

    const countOf = (value) => value && value.rows ? { ready: value.rows.filter((r) => r.readiness === "READY").length, total: value.rows.length } : null;
    function httpGroups(value) {
        if (!value || !value.rows) return null;
        const counts = {};
        value.rows.forEach((row) => { const status = row.httpStatus || "UNAVAILABLE"; counts[status] = (counts[status] || 0) + 1; });
        const state = { PASSED: "ok", FAILED: "bad", NOT_CONFIGURED: "muted", UNAVAILABLE: "muted" };
        return Object.entries(counts).map(([status, count]) => ({ text: `${status === "NOT_CONFIGURED" ? "Not configured" : plainStrategy(status)} ×${count}`, state: state[status] || "neutral" }));
    }
    function quantity(value) {
        if (!finite(value)) return null;
        return value >= 100 ? Math.round(value).toLocaleString("en-US") : value >= 1 ? String(Number(value.toFixed(1))) : String(Number(value.toFixed(3)));
    }
    /** Before → after for a sampled resource. Neutral on purpose: the runs can have different workloads, so a rise is not a verdict. */
    function usageCell(before, after, unit) {
        const a = quantity(before), b = quantity(after);
        let delta = null;
        if (finite(before) && finite(after) && before > 0) {
            const change = (after - before) / before * 100;
            delta = `${change >= 0 ? "+" : "−"}${Math.abs(change) >= 100 ? Math.round(Math.abs(change)).toLocaleString("en-US") : Number(Math.abs(change).toFixed(1))}%`;
        }
        return { kind: "usage", before: a, after: b, unit, delta, key: `${a}|${b}` };
    }
    const mostCommon = (values) => {
        const counts = new Map();
        values.filter(Boolean).forEach((value) => counts.set(value, (counts.get(value) || 0) + 1));
        return [...counts.entries()].sort((x, y) => y[1] - x[1])[0]?.[0] || null;
    };

    /**
     * The comparison as data: columns, then sections of rows whose cells say what to draw. Nothing is computed that the saved run does not
     * contain; a missing value stays a missing value ("unknown"), and a row is "same" only when every run shows the same thing.
     */
    function buildComparison(rows, strategyName = plainStrategy) {
        const uids = rows.map((r) => r.clusterUid || null);
        const digests = rows.map((r) => r.workflowSetDigest || null);
        const homeUid = mostCommon(uids), homeDigest = mostCommon(digests);
        const columns = rows.map((row, i) => ({ ...candidateTitle(row, strategyName), candidateId: row.candidateId, runId: row.runId,
            otherCluster: !!uids[i] && !!homeUid && uids[i] !== homeUid }));
        const text = (value, title) => ({ kind: "text", text: value, title: title || null, key: String(value) });
        const identifier = (value) => ({ kind: "id", text: shortId(value), full: value || null, key: String(value) });
        const row = (label, cells, collapsible = true) => ({ label, cells, collapsible, same: cells.every((c) => c.key === cells[0].key) });
        const risk = (r) => {
            const before = finite(r.baselineRisk) ? r.baselineRisk : null, predicted = finite(r.predictedRisk) ? r.predictedRisk : null, after = finite(r.afterRisk) ? r.afterRisk : null;
            const mismatch = predicted !== null && after !== null && Math.abs(predicted - after) > RISK_MATCH_TOLERANCE;
            return { kind: "risk", before, predicted, after, mismatch, key: `${before}|${predicted}|${after}` };
        };
        const readiness = (r) => {
            const before = countOf(r.beforeWorkflows), after = countOf(r.afterWorkflows);
            const worse = !!before && !!after && after.ready < before.ready;
            return { kind: "ready", before, after, state: !after ? "unknown" : worse ? "bad" : after.total && after.ready === after.total ? "ok" : "neutral",
                key: JSON.stringify([before, after]) };
        };
        const http = (r) => { const before = httpGroups(r.beforeWorkflows), after = httpGroups(r.afterWorkflows); return { kind: "http", before, after, key: JSON.stringify([before, after]) }; };
        const sampled = (r) => `${r.beforeResources === true ? "available" : r.beforeResources === false ? "unavailable" : "not collected"} (${r.beforePodUsage?.sampledPods ?? "?"} pods) → ` +
            `${r.afterResources === true ? "available" : r.afterResources === false ? "unavailable" : "not collected"} (${r.afterPodUsage?.sampledPods ?? "?"} pods)`;
        const sections = [
            { title: "Run", rows: [
                row("Run", rows.map((r) => identifier(r.runId)), false),
                row("Started", rows.map((r) => ({ kind: "time", text: friendlyTime(r.createdAt), title: r.createdAt ? `${r.createdAt} UTC` : null, key: String(r.createdAt) })), false)] },
            { title: "Outcome", rows: [row("Result", rows.map((r) => { const [label, state] = resultLabel(r); return { kind: "chip", text: outcomeLabel(r), state, key: label }; }), false)] },
            { title: "What the runs were measured on", rows: [
                row("Cluster", rows.map((r, i) => ({ ...identifier(r.clusterUid), kind: "id", differs: !!uids[i] && !!homeUid && uids[i] !== homeUid, key: String(r.clusterUid) }))),
                row("Workflow version", rows.map((r, i) => ({ ...identifier(r.workflowSetDigest), differs: !!digests[i] && !!homeDigest && digests[i] !== homeDigest, key: String(r.workflowSetDigest) })))] },
            { title: "Risk (model)", rows: [row("Before / predicted / after", rows.map(risk), false)] },
            { title: "Readiness and checks", rows: [row("Pods ready", rows.map(readiness)), row("Workflow requests", rows.map(http))] },
            { title: "Sampled resources", rows: [
                row("CPU", rows.map((r) => usageCell(r.beforePodUsage?.cpuMillicores, r.afterPodUsage?.cpuMillicores, "mCPU"))),
                row("Memory", rows.map((r) => usageCell(r.beforePodUsage?.memoryMiB, r.afterPodUsage?.memoryMiB, "MiB"))),
                row("Samples", rows.map((r) => text(sampled(r))))] },
        ];
        return { columns, sections, clusterMismatch: new Set(uids.filter(Boolean)).size > 1 };
    }

    function createHistoryPanel(env) {
        const { root, document, fetchImpl } = env;
        let rows = [], more = false, loading = false, error = null, viewer = null, closed = false;
        let hideSame = false;       // a view choice only: it hides rows that read the same in every selected run
        let selected = new Set();
        try { selected = new Set(JSON.parse(env.storage?.getItem("resilience.apply-test.compare.v1") || "[]")); } catch (_error) { /* Optional storage. */ }
        const make = (tag, text) => { const node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = String(text); return node; };
        const label = (row) => row.candidate ? `${row.candidate.type} on ${row.candidate.service}` : row.candidateId;
        function button(text, action) { const node = make("button", text); node.type = "button"; node.className = "secondary-button"; node.addEventListener("click", action); return node; }
        function grid(headers, entries) {
            const wrap = make("div"), table = make("table"), head = make("thead"), line = make("tr"), body = make("tbody");
            wrap.className = "table-scroll"; table.className = "data-table";
            if (headers[0] === "Metric") table.className += " test-history__comparison";
            headers.forEach((title) => { const cell = make("th", title); cell.setAttribute("scope", "col"); line.append(cell); });
            head.append(line);
            entries.forEach((entry) => { const tr = make("tr"); entry.forEach((value) => { const cell = make("td"); if (value && typeof value === "object") cell.append(value); else cell.textContent = value ?? "Unavailable"; tr.append(cell); }); body.append(tr); });
            table.append(head, body); wrap.append(table); return wrap;
        }
        const detailRoot = make("section"); detailRoot.setAttribute("aria-label", "Saved test report");
        function open(row) {
            viewer?.destroy();
            viewer = createPanel({ root: detailRoot, document, fetchImpl, runId: row.runId, readOnly: true, download: env.download });
            return viewer.start();
        }

        const el = (tag, text, cls, attrs) => {
            const node = make(tag, text);
            if (cls) node.className = cls;
            for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
            return node;
        };
        const none = (what = "Unavailable") => el("span", "—", "cmp-none", { title: what, "aria-label": what });
        const chip = (text, state) => el("span", text, "cmp-chip", { "data-state": state || "neutral" });
        function copyButton(value, what) {
            const node = el("button", "Copy", "cmp-copy", { type: "button", "aria-label": `Copy ${what}`, title: `Copy the full ${what}` });
            node.addEventListener("click", async () => { try { await env.clipboard?.writeText?.(value) ?? globalThis.navigator?.clipboard?.writeText?.(value); node.textContent = "Copied"; } catch (_error) { node.textContent = "Not copied"; } });
            return node;
        }
        function riskBars(cell) {
            const wrap = el("div", null, "cmp-risk");
            [["Before", cell.before], ["Predicted", cell.predicted], ["After", cell.after]].forEach(([name, value]) => {
                const line = el("div", null, "cmp-risk__line");
                line.append(el("span", name, "cmp-risk__name"));
                const track = el("span", null, "cmp-risk__track", { "aria-hidden": "true" });
                if (value !== null) track.append(el("span", null, "cmp-risk__fill", { style: `width:${Math.max(0, Math.min(100, value))}%`, "data-kind": name.toLowerCase() }));
                line.append(track, value === null ? none() : el("span", `${value}%`, "cmp-risk__value"));
                wrap.append(line);
            });
            if (cell.mismatch) wrap.append(chip("Measured differs from predicted", "bad"));
            return wrap;
        }
        function renderCell(cell) {
            const wrap = el("div", null, `cmp-cell cmp-cell--${cell.kind}`);
            if (cell.kind === "id") {
                if (!cell.full) { wrap.append(none()); return wrap; }
                wrap.append(el("code", cell.text, "cmp-id", { title: cell.full }));
                if (cell.differs) wrap.append(chip("Different", "bad"));
                wrap.append(copyButton(cell.full, "value"));
            } else if (cell.kind === "time") wrap.append(cell.text ? el("span", cell.text, null, { title: cell.title || "" }) : none());
            else if (cell.kind === "chip") wrap.append(chip(cell.text, cell.state));
            else if (cell.kind === "risk") wrap.append(riskBars(cell));
            else if (cell.kind === "ready") {
                const part = (c) => (c ? el("span", `${c.ready}/${c.total}`) : none("Not collected"));
                wrap.append(part(cell.before), el("span", "→", "cmp-arrow", { "aria-hidden": "true" }), part(cell.after));
                wrap.setAttribute("data-state", cell.state);
            } else if (cell.kind === "http") {
                const group = (items) => { const g = el("span", null, "cmp-chips"); (items || []).forEach((i) => g.append(chip(i.text, i.state))); return items ? g : none("Not collected"); };
                wrap.append(group(cell.before), el("span", "→", "cmp-arrow", { "aria-hidden": "true" }), group(cell.after));
            } else if (cell.kind === "usage") {
                const side = (value) => (value === null ? none() : el("span", value));
                wrap.append(side(cell.before), el("span", "→", "cmp-arrow", { "aria-hidden": "true" }), side(cell.after));
                if (cell.before !== null && cell.after !== null) wrap.append(el("span", cell.unit, "cmp-unit"));
                if (cell.delta) wrap.append(chip(cell.delta, "neutral"));
            } else wrap.append(cell.text ? el("span", cell.text, null, { title: cell.title || "" }) : none());
            return wrap;
        }
        function comparisonView(chosen) {
            const model = buildComparison(chosen, env.strategyName || plainStrategy);
            const root = el("div", null, "cmp");
            if (model.clusterMismatch) root.append(el("p", "These runs come from different clusters, so they are not directly comparable. Columns marked “Different cluster” differ from the rest.", "cmp-warning", { role: "note" }));
            const toggle = el("label", null, "cmp-toggle");
            const box = el("input"); box.type = "checkbox"; box.checked = hideSame;
            box.addEventListener("change", () => { hideSame = box.checked; draw(); });
            toggle.append(box, el("span", " Hide rows that are the same in every run"));
            root.append(toggle);
            const wrap = el("div", null, "table-scroll cmp-scroll"), table = el("table", null, "data-table cmp-table");
            const head = el("tr"), selectedId = env.selectedId?.();
            head.append(el("th", "Metric", "cmp-corner", { scope: "col" }));
            model.columns.forEach((c) => {
                const th = el("th", null, "cmp-head", { scope: "col", "data-selected": String(!!selectedId && c.candidateId === selectedId) });
                if (c.strategy) th.append(el("span", c.strategy, "cmp-strategy"));
                th.append(el("span", c.services, "cmp-services", { title: c.id || "" }));
                if (c.otherCluster) th.append(chip("Different cluster", "bad"));
                if (selectedId && c.candidateId === selectedId) th.append(chip("Selected", "ok"));
                head.append(th);
            });
            const thead = el("thead"); thead.append(head); table.append(thead);
            const body = el("tbody");
            model.sections.forEach((section) => {
                const visible = section.rows.filter((r) => !(hideSame && r.collapsible && r.same && chosen.length > 1));
                if (!visible.length) return;
                const band = el("tr", null, "cmp-section"), title = el("th", section.title, null, { scope: "colgroup", colspan: String(model.columns.length + 1) });
                band.append(title); body.append(band);
                visible.forEach((r) => {
                    const line = el("tr", null, r.same && chosen.length > 1 ? "cmp-same" : "");
                    line.append(el("th", r.label, "cmp-label", { scope: "row" }));
                    r.cells.forEach((cell) => { const td = el("td"); td.append(renderCell(cell)); line.append(td); });
                    body.append(line);
                });
            });
            table.append(body); wrap.append(table); root.append(wrap);
            return root;
        }
        function draw() {
            root.replaceChildren(make("h3", "Test history and comparison"),
                make("p", "Every test is saved, including interrupted and failed runs. Reset does not delete results. Select tests below to compare their recorded evidence."));
            root.append(button(loading ? "Loading history…" : "Refresh test history", () => load(false)));
            if (error) { const message = make("p", error); message.setAttribute("role", "alert"); root.append(message); }
            if (!rows.length) root.append(make("p", loading ? "Reading saved tests…" : "No saved candidate tests yet."));
            else root.append(grid(["Compare", "Candidate", "Started", "Cluster / application", "Result", "Report"], rows.map((row) => {
                const check = make("input"); check.type = "checkbox"; check.checked = selected.has(row.runId);
                check.setAttribute("aria-label", `Compare ${label(row)} ${row.createdAt}`);
                check.addEventListener("change", () => {
                    check.checked ? selected.add(row.runId) : selected.delete(row.runId);
                    try { env.storage?.setItem("resilience.apply-test.compare.v1", JSON.stringify([...selected])); } catch (_error) { /* Optional storage. */ }
                    draw();
                });
                const title = candidateTitle(row, env.strategyName || plainStrategy), when = friendlyTime(row.createdAt);
                const name = el("div", null, "cmp-head");
                if (title.strategy) name.append(el("span", title.strategy, "cmp-strategy"));
                name.append(el("span", title.services, "cmp-services", { title: title.id || "" }));
                const time = el("span", when || "", null, { title: row.createdAt ? `${row.createdAt} UTC` : "" });
                return [check, name, when ? time : none(), `${row.clusterName || "Unknown cluster"} / ${row.namespace}`, chip(outcomeLabel(row), resultLabel(row)[1]),
                    button("View saved report", () => open(row))];
            })));
            if (more) root.append(button("Load older tests", () => load(true)));
            const chosen = rows.filter((row) => selected.has(row.runId));
            if (chosen.length) {
                root.append(make("h4", "Selected test comparison"), make("p", "These are separate runs, not a controlled ranking. Compare cluster UID, workflow version and baseline risk. Resource totals sum recorded pod samples; workloads and sample times can differ. Missing checks and interrupted runs are not passes."));
                root.append(comparisonView(chosen));
            }
            root.append(detailRoot);
        }
        async function load(older = false) {
            if (loading || closed) return;
            loading = true; error = null; draw();
            try {
                const response = await fetchImpl(`${BASE}/history?limit=20&offset=${older ? rows.length : 0}`);
                const value = await response.json();
                if (!response.ok) throw new Error(value.detail?.message || "Saved test history could not be read.");
                if (closed) return;
                const incoming = value.runs || [];
                rows = older ? [...new Map([...rows, ...incoming].map((row) => [row.runId, row])).values()] : incoming;
                more = !!value.hasMore;
            } catch (e) { error = e.message; }
            loading = false;
            if (!closed) {
                env.onRows?.(rows, { hasMore: more, error, historyLoaded: !error || rows.length > 0 });
                draw();
            }
        }
        draw();
        return { start: () => load(false), refresh: () => load(false), loadOlder: () => load(true), redraw: draw,
            async openRun(runId) {
                const row = rows.find((item) => item.runId === runId);
                if (!row) return;
                await open(row);
                try { detailRoot.scrollIntoView?.({ block: "start", behavior: "smooth" }); } catch (_error) { /* Optional presentation. */ }
            },
            destroy() { closed = true; viewer?.destroy(); } };
    }
    const RESULT_STATUSES = { COMPLETED: ["Checks passed", "passed"], COMPLETED_WITH_GAPS: ["Missing checks", "gaps"],
        TESTS_FAILED: ["Checks failed", "failed"], FAILED: ["Run stopped", "failed"], INTERRUPTED: ["Interrupted", "gaps"], RUNNING: ["Running", "running"], QUEUED: ["Queued", "running"] };
    /** [label, state] of a saved run. A missed risk target takes precedence over a completed run: functional checks never hide it. */
    const resultLabel = (row) => row.riskTargetMet === false ? ["Risk target not met", "failed"] : RESULT_STATUSES[row.status] || [row.status, "unknown"];
    function createCandidateResultsSidebar(env) {
        const { root, document } = env;
        const make = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined && text !== null) node.textContent = String(text); if (cls) node.className = cls; return node; };
        function update(state) {
            const scrollTop = root.scrollTop;
            const { candidates = [], history = [], namespace, selectedId, activeId, activeRun, historyLoaded, error, hasMore } = state;
            const saved = history.filter((row) => !namespace || row.namespace === namespace);
            const latest = new Map();
            saved.forEach((row, index) => { if (!latest.has(row.candidateId)) latest.set(row.candidateId, { row, index }); });
            const entries = new Map(candidates.map((candidate) => [candidate.id, candidate]));
            for (const row of saved) if (!entries.has(row.candidateId)) entries.set(row.candidateId, { id: row.candidateId, ...row.candidate, historical: true });
            const percent = (value) => Number.isFinite(value) ? `${value}%` : "Unavailable";
            root.replaceChildren(make("h3", "Candidate results"), make("p", "Select a fix to see its data. Active marks the change applied to the cluster. Risk remains a model prediction.", "rec-results-sidebar__hint"));
            if (namespace) root.append(make("p", namespace, "rec-results-sidebar__scope"));
            if (error) root.append(make("p", "Saved results could not be refreshed. Open Test history to retry.", "ui-note"));
            if (!entries.size) root.append(make("p", historyLoaded ? "No candidates or saved tests yet." : "Reading candidates and saved tests…", "ui-note"));
            const list = make("div", undefined, "rec-results-sidebar__list");
            const ordered = [...entries.values()].sort((a, b) => {
                if ((a.id === activeId) !== (b.id === activeId)) return a.id === activeId ? -1 : 1;   // the applied change stays on top
                const aRank = latest.get(a.id)?.index, bRank = latest.get(b.id)?.index;
                if (aRank === undefined) return bRank === undefined ? 0 : 1;
                return bRank === undefined ? -1 : aRank - bRank;
            });
            for (const candidate of ordered) {
                const savedRun = latest.get(candidate.id)?.row;
                const card = make("article", undefined, "rec-result-item");
                const isActive = !!activeId && candidate.id === activeId;
                const isSelected = candidate.id === selectedId;
                card.setAttribute("data-selected", String(isSelected));
                card.setAttribute("data-active", String(isActive));
                const title = candidate.type ? env.strategyName(candidate.type) : candidate.id;
                card.append(make("h4", title));
                if (isActive || isSelected) {
                    const tags = make("p", undefined, "rec-result-item__tags");
                    if (isActive) tags.append(make("span", "Active", "rec-result-item__tag rec-result-item__tag--active"));
                    if (isSelected) tags.append(make("span", "Selected", "rec-result-item__tag"));
                    card.append(tags);
                }
                if (candidate.service) card.append(make("p", `on ${candidate.service}`, "rec-result-item__target"));
                const status = savedRun ? resultLabel(savedRun)
                    : [error ? "Results unavailable" : !historyLoaded ? "Reading results…" : hasMore ? "Older results not loaded" : "Not tested", "unknown"];
                const badge = make("span", status[0], "rec-result-item__badge"); badge.setAttribute("data-state", status[1]); card.append(badge);
                if (isActive && activeRun) {
                    // The applied change: where the run stands and what it measured so far, from the run itself.
                    const report = activeRun.report || {};
                    const data = make("dl", undefined, "rec-result-item__data");
                    const row = (name, value) => { data.append(make("dt", name), make("dd", value)); };
                    row("Run", outcomeLabel(activeRun));
                    if (activeRun.current?.message) row("Now", activeRun.current.message);
                    row("Model risk before / predicted / after", `${percent(report.baselineRisk?.blastRadius)} / ${percent(report.predictedRisk)} / ${percent(report.riskAnalysis?.observed?.blastRadius)}`);
                    if (report.before?.workflows?.rows) row("Readiness before → after", `${report.before.workflows.rows.filter((r) => r.readiness === "READY").length}/${report.before.workflows.rows.length} → ${report.after?.workflows?.rows ? `${report.after.workflows.rows.filter((r) => r.readiness === "READY").length}/${report.after.workflows.rows.length}` : "Not collected"}`);
                    card.append(data);
                } else if (isSelected && candidate.predicted) {
                    // A selected candidate that is not applied: what the simulation predicts, never a measurement.
                    const data = make("dl", undefined, "rec-result-item__data");
                    const row = (name, value) => { data.append(make("dt", name), make("dd", value)); };
                    row("Predicted risk now → after", `${percent(candidate.predicted.blastRadiusBefore)} → ${percent(candidate.predicted.blastRadiusAfter)}`);
                    if (candidate.cost?.resource) row("Cost", candidate.cost.resource);
                    if (candidate.recommendedChange) row("Change", candidate.recommendedChange);
                    card.append(data, make("p", "Prediction from the simulation, not a measurement.", "rec-result-item__meta"));
                }
                if (savedRun) {
                    if (candidate.yamlProposal?.sha256 && candidate.yamlProposal.sha256 !== savedRun.yamlSha256) card.append(make("p", "Earlier proposal", "rec-result-item__meta"));
                    const checks = savedRun.afterWorkflows?.rows;
                    card.append(make("p", checks ? checks.map((row) => `${row.workflowId}: ${row.httpStatus || "Unavailable"}`).join(" · ") : "Post-apply workflow checks not collected.", "rec-result-item__meta"));
                    card.append(make("p", `${savedRun.namespace} · cluster ${savedRun.clusterUid || "unverified"}`, "rec-result-item__meta"));
                    card.append(make("p", `${savedRun.createdAt} UTC`, "rec-result-item__meta"));
                    const report = make("button", "View saved report", "rec-link"); report.type = "button";
                    report.addEventListener("click", () => env.onReport(savedRun.runId)); card.append(report);
                }
                // The page decides what may be selected (its rule depends on the workflow's tier); the same rule gates the click.
                const selectable = !candidate.historical && (env.canSelect ? env.canSelect(candidate) : (candidate.researchApproval?.available || candidate.approval?.ready));
                if (selectable) {
                    const choose = make("button", candidate.id === selectedId ? "Selected" : "Select candidate", "secondary-button"); choose.type = "button";
                    choose.disabled = candidate.id === selectedId;
                    choose.addEventListener("click", () => env.onSelect(candidate.id)); card.append(choose);
                } else if (!candidate.historical) card.append(make("p", env.whyNotSelectable?.(candidate) || "Unavailable for the current placement", "rec-result-item__meta"));
                list.append(card);
            }
            root.append(list);
            if (hasMore) { const older = make("button", "Load older saved results", "secondary-button"); older.type = "button"; older.addEventListener("click", env.onLoadOlder); root.append(older); }
            root.scrollTop = scrollTop;
        }
        return { update };
    }
    window.ResilienceApplyTest = { createPanel, createHistoryPanel, createCandidateResultsSidebar, resultLabel,
        compare: { buildComparison, shortId, friendlyTime, candidateTitle, usageCell, RISK_MATCH_TOLERANCE } };
})();
