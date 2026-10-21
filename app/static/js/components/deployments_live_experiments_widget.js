/**
 * Live Experiments Widget: shows the stored live placement-drift runs on KinD
 * (GET /api/deployments/benchmarks/live-experiments). Values are shown as stored; nothing is computed here
 * except counts and formatting.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsLiveExperimentsWidget = (function () {
    "use strict";

    const CONFIGS = ["A-no-rule", "B-spread"];

    const pct = (v) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function statWithCi(label, stat) {
        if (stat?.mean == null) return `${label}: n/a`;
        const ci = stat.ci95 ? ` [${pct(stat.ci95[0])}, ${pct(stat.ci95[1])}]` : "";
        return `${label}: ${pct(stat.mean)}${ci}`;
    }

    function renderCell(cond) {
        const td = el("td");
        if (!cond) {
            td.textContent = "not run";
            return td;
        }
        const ok = cond.runs - cond.failed_runs;
        const badges = el("div");
        badges.style.display = "flex";
        badges.style.flexWrap = "wrap";
        badges.style.gap = "6px";

        Object.entries(cond.target_status_t1_counts || {}).forEach(([status, count]) => {
            let variant = "muted";
            if (status === "EXPOSED") variant = "danger";
            else if (status === "NOT_EXPOSED") variant = "success";
            badges.appendChild(el("span", `status-badge status-badge--${variant}`, `${status.replace("_", " ")} ${count}/${ok}`));
        });
        if (cond.unstable_runs > 0) {
            badges.appendChild(el("span", "status-badge status-badge--warning", `Pods stuck ${cond.unstable_runs}/${ok}`));
        }
        if (cond.failed_runs > 0) {
            badges.appendChild(el("span", "status-badge status-badge--danger", `Failed ${cond.failed_runs}/${cond.runs}`));
        }
        td.appendChild(badges);

        const details = el("div");
        details.style.fontSize = "12px";
        details.style.color = "var(--color-text-muted)";
        details.style.marginTop = "6px";
        details.appendChild(el("div", null, statWithCi("D_rate", cond.d_rate)));
        details.appendChild(el("div", null, `Exposure rate: ${pct(cond.exposure_rate_t0.mean)} → ${pct(cond.exposure_rate_t1.mean)}`));
        if (cond.compliance_rate_t1?.mean != null) {
            details.appendChild(el("div", null, `Compliance: ${pct(cond.compliance_rate_t1.mean)}`));
        }
        td.appendChild(details);
        return td;
    }

    function renderSummary(container, data) {
        container.innerHTML = "";
        const conds = data.conditions;
        const exposedConds = conds.filter((c) => c.target_became_exposed > 0).length;
        const stuckConds = conds.filter((c) => c.unstable_runs > 0).length;
        const repetitions = conds.length ? Math.max(...conds.map((c) => c.runs)) : 0;

        const items = [
            { label: "Live Runs", value: `${data.total_runs}`, sub: `${conds.length} conditions × ${repetitions} repetitions; ${data.failed_runs} failed` },
            { label: "Target Became Exposed", value: `${exposedConds} / ${conds.length}`, sub: `Conditions where ${data.target_service} went NOT_EXPOSED → EXPOSED` },
            { label: "Pods Stuck", value: `${stuckConds} / ${conds.length}`, sub: "Conditions with runs that did not settle within 90 s" },
        ];
        items.forEach((item) => {
            const box = el("div", "ensemble-metric-box");
            box.append(
                el("span", "ensemble-metric-box__label", item.label),
                el("span", "ensemble-metric-box__value", item.value),
                el("span", "ensemble-metric-box__sub", item.sub),
            );
            container.appendChild(box);
        });
    }

    const CONFIG_LABELS = { "A-no-rule": "no placement rule", "B-spread": "topology spread rule" };

    // Each sentence is derived from the stored counts; nothing here is typed in by hand.
    function buildFindings(data) {
        const conds = data.conditions;
        const target = data.target_service;
        const label = (c) => `${c.scenario_name} (${CONFIG_LABELS[c.config] || c.config})`;
        const okRuns = (c) => c.runs - c.failed_runs;
        const findings = [];

        conds.filter((c) => c.target_became_exposed > 0).forEach((c) => {
            const health = c.unstable_runs === 0
                ? "while every replica was Ready and the rollout reported complete"
                : `while pods were stuck Pending in ${c.unstable_runs}/${okRuns(c)} runs`;
            findings.push({
                severity: "danger",
                text: `${label(c)}: ${target} went from NOT_EXPOSED to EXPOSED in ${c.target_became_exposed}/${okRuns(c)} runs, ${health}.`,
            });
        });

        conds.filter((c) => c.target_became_exposed > 0 && c.compliance_rate_t1?.mean === 1).forEach((c) => {
            findings.push({
                severity: "danger",
                text: `${label(c)}: the declared rule was satisfied (compliance ${pct(c.compliance_rate_t1.mean)}), yet ${target} was exposed. Rule compliance alone did not mean resilience.`,
            });
        });

        const stuck = conds.filter((c) => c.unstable_runs > 0 && c.target_became_exposed === 0);
        const stuckList = stuck.map((c) => label(c) + " " + c.unstable_runs + "/" + okRuns(c)).join("; ");
        if (stuck.length) {
            findings.push({
                severity: "warning",
                text: "Pods stayed Pending (workload not fully ready within 90 s) in: " + stuckList + ".",
            });
        }

        const unchanged = conds.filter((c) => c.d_rate?.mean === 0 && c.target_became_exposed === 0);
        findings.push({
            severity: "success",
            text: `In ${unchanged.length} of ${conds.length} conditions no service changed exposure status in any run (D_rate 0).`,
        });

        const repeatable = conds.filter((c) => c.d_rate?.sd === 0).length;
        findings.push({
            severity: "muted",
            text: `${repeatable} of ${conds.length} conditions gave the same D_rate in every repetition.`,
        });
        return findings;
    }

    function renderFindings(list, data) {
        list.innerHTML = "";
        const colors = { danger: "var(--color-danger)", warning: "#b45309", success: "var(--color-success)", muted: "var(--color-text-muted)" };
        buildFindings(data).forEach((f) => {
            const li = el("li", null, f.text);
            li.style.color = colors[f.severity];
            if (f.severity === "danger") li.style.fontWeight = "600";
            list.appendChild(li);
        });
    }

    // One placement snapshot as node columns; pods without a node are shown as Pending.
    // Concentration = largest share of the target's ready replicas on one node (stored by the run).
    function concentrationText(conc) {
        if (conc?.ratio == null) return "concentration n/a (no ready replica)";
        const onBusiest = Math.round(conc.ratio * conc.ready_replicas);
        return `concentration ${Math.round(conc.ratio * 100)}% (${onBusiest} of ${conc.ready_replicas} ready on one node)`;
    }

    // Target service first, then the others alphabetically.
    const targetFirst = (target) => (a, b) => {
        if (a.svc === target) return -1;
        if (b.svc === target) return 1;
        return a.svc.localeCompare(b.svc);
    };

    function placementGrid(title, snapshot, target, statusText, conc, previousConc) {
        const wrap = el("div", "placement-snap");
        const head = el("div", "placement-snap__title");
        head.append(el("strong", null, title), el("span", null, ` · ${target}: ${statusText} · `));
        const concSpan = el("span", null, concentrationText(conc));
        const rose = previousConc?.ratio != null && conc?.ratio != null && conc.ratio > previousConc.ratio;
        if (rose) {
            concSpan.className = "placement-snap__conc-up";
            concSpan.textContent += " ⚠️ more concentrated";
            concSpan.title = "Not exposed yet, but losing the busiest node now removes more replicas than before.";
        }
        head.appendChild(concSpan);
        wrap.appendChild(head);

        const byNode = {};
        Object.entries(snapshot).forEach(([svc, pods]) => {
            pods.forEach((p) => {
                const key = p.node || "Pending (no node)";
                (byNode[key] = byNode[key] || []).push({ svc, ready: p.ready });
            });
        });
        // Show "worker2" instead of "resilience-research-worker2"; the full name is in the tooltip.
        // Pods with no node (stored as "unscheduled" or null) are Pending: created but not placed anywhere.
        const PENDING_KEYS = ["unscheduled", "Pending (no node)"];
        const short = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");
        const cols = el("div", "placement-snap__cols");
        Object.keys(byNode).sort((a, b) => a.localeCompare(b)).forEach((node) => {
            const pending = PENDING_KEYS.includes(node);
            const col = el("div", pending ? "placement-snap__col placement-snap__col--pending" : "placement-snap__col");
            const label = el("div", "placement-snap__node", pending ? "⏳ Waiting: no node found (Pending)" : `🖥️ ${short(node)}`);
            label.title = pending ? "Kubernetes created these pods but could not place them on any node" : node;
            col.appendChild(label);
            byNode[node].sort(targetFirst(target)).forEach((p) => {
                let cls = "placement-pod";
                if (p.svc === target) cls += " placement-pod--target";
                if (!p.ready) cls += " placement-pod--notready";
                col.appendChild(el("span", cls, `${p.svc.replace("service", "")}${p.ready ? "" : " (not ready)"}`));
            });
            cols.appendChild(col);
        });
        wrap.appendChild(cols);
        return wrap;
    }

    function detailRow(row, target) {
        const tr = el("tr", "live-exp-detail");
        const td = el("td");
        td.colSpan = 3;
        CONFIGS.forEach((cfg) => {
            const run = row.configs[cfg]?.example_run;
            const box = el("div", "live-exp-detail__config");
            box.appendChild(el("h5", null, `${CONFIG_LABELS[cfg] || cfg} — run ${run ? run.repetition : "n/a"}`));
            if (!run) {
                box.appendChild(el("p", "ui-note", "No stored run."));
            } else {
                box.appendChild(el("p", "ui-note", `Action: ${run.action.action}${run.stable_after_disruption ? "" : " · did not settle within 90 s"}`));
                const pair = el("div", "placement-pair");
                const c0 = (run.concentration_t0 || {})[target];
                const c1 = (run.concentration_t1 || {})[target];
                pair.append(
                    placementGrid("Before (T0)", run.t0, target, run.target_status_t0, c0, null),
                    el("div", "placement-pair__arrow", "→"),
                    placementGrid("After (T1)", run.t1, target, run.target_status_t1, c1, c0),
                );
                box.appendChild(pair);
            }
            td.appendChild(box);
        });
        tr.appendChild(td);
        return tr;
    }

    function renderTable(tbody, data) {
        tbody.innerHTML = "";
        const byScenario = new Map();
        data.conditions.forEach((c) => {
            if (!byScenario.has(c.scenario_id)) byScenario.set(c.scenario_id, { name: c.scenario_name, configs: {} });
            byScenario.get(c.scenario_id).configs[c.config] = c;
        });

        [...byScenario.keys()].sort((a, b) => a.localeCompare(b)).forEach((id) => {
            const row = byScenario.get(id);
            const tr = el("tr");
            const nameCell = el("td");
            nameCell.appendChild(el("div", null, row.name)).style.fontWeight = "600";
            nameCell.appendChild(el("code", null, id)).style.fontSize = "11px";
            tr.appendChild(nameCell);
            CONFIGS.forEach((cfg) => tr.appendChild(renderCell(row.configs[cfg])));
            tr.className = "live-exp-row";
            tr.title = "Click to show the pods before and after";
            const detail = detailRow(row, data.target_service);
            detail.hidden = true;
            tr.addEventListener("click", () => {
                detail.hidden = !detail.hidden;
                tr.classList.toggle("is-open", !detail.hidden);
            });
            tbody.append(tr, detail);
        });
    }

    async function load() {
        const root = document.getElementById("tab-live-experiments");
        if (!root) return;
        const note = root.querySelector("[data-role='live-exp-note']");
        const summary = root.querySelector("[data-role='live-exp-summary']");
        const tbody = root.querySelector("[data-role='live-exp-table-body']");
        const findings = root.querySelector("[data-role='live-exp-findings']");

        try {
            const resp = await fetch("/api/deployments/benchmarks/live-experiments");
            if (!resp.ok) {
                note.textContent = resp.status === 404
                    ? "No live experiment results stored yet. Run experiments/placement_drift/run_live_experiments.py."
                    : `Could not load live experiment results (HTTP ${resp.status}).`;
                return;
            }
            const data = await resp.json();
            note.textContent = `${data.evidence_note} Source: ${data.results_source}`;
            renderFindings(findings, data);
            renderSummary(summary, data);
            renderTable(tbody, data);
        } catch (err) {
            console.warn("Failed to load live experiment results:", err);
            note.textContent = "Could not load live experiment results.";
        }
    }

    // ---------------------------------------------------------------------------------------------------------
    // Simulation on the connected cluster
    //
    // Runs the eight disruption scenarios (GET /api/deployments/chaos/benchmarks?source=live) on the real
    // topology of one namespace of the connected cluster. It is a prediction by a fixed rule, not an
    // observation, and nothing in the cluster changes. The response is shown as live only when the server
    // says its topology was live; anything else is refused, never relabelled.
    // ---------------------------------------------------------------------------------------------------------

    const OUTCOME_VARIANT = { COMPLETED: "success", FAILED: "danger", ABORTED: "danger" };
    let simulationInFlight = false;
    let simulationRerun = false;

    const api = () => window.ResilienceApi || { fetchJson: (url) => fetch(url).then((r) => r.json()) };

    function simulationParts() {
        const root = document.getElementById("tab-live-experiments");
        if (!root) return null;
        return {
            select: root.querySelector("[data-role='live-sim-namespace']"),
            run: root.querySelector("[data-role='live-sim-run']"),
            status: root.querySelector("[data-role='live-sim-status']"),
            results: root.querySelector("[data-role='live-sim-results']"),
            body: root.querySelector("[data-role='live-sim-body']"),
        };
    }

    function setSimulationStatus(parts, message, isError) {
        parts.status.textContent = message;
        parts.status.style.color = isError ? "var(--color-danger)" : "var(--color-text-muted)";
    }

    // The namespace with the most services is the default: it is the one most likely to be the workload.
    function chooseNamespace(rows) {
        let best = null;
        rows.forEach((row) => {
            const count = (row.graph?.services || []).length;
            if (count > 0 && (!best || count > best.count)) best = { name: row.namespace, count };
        });
        return best ? best.name : null;
    }

    function renderSimulationRows(parts, payload) {
        parts.body.replaceChildren();
        const results = payload.scenario_results || [];
        results.forEach((result) => {
            const row = el("tr");
            row.appendChild(el("td", null, String(result.scenario_name || result.scenario_id).replace(" (live topology)", "")));
            const outcome = el("td");
            outcome.appendChild(el("span", `status-badge status-badge--${OUTCOME_VARIANT[result.status] || "muted"}`, result.status));
            if (result.error_message) outcome.appendChild(el("div", null, result.error_message)).style.fontSize = "12px";
            row.appendChild(outcome);
            row.appendChild(el("td", null, pct(result.d_rate)));
            const exposure = el("td");
            if (result.delta_e_rate == null) {
                exposure.textContent = "n/a";
            } else {
                const sign = result.delta_e_rate > 0 ? "+" : "";
                exposure.appendChild(el("span", `status-badge status-badge--${result.delta_e_rate > 0 ? "danger" : "muted"}`,
                    `${sign}${(result.delta_e_rate * 100).toFixed(1)}%`));
            }
            row.appendChild(exposure);
            row.appendChild(el("td", null, (result.drifted_services || []).join(", ") || "none"));
            row.appendChild(el("td", null, String((result.transitions || []).length)));
            parts.body.appendChild(row);
        });
        parts.results.hidden = results.length === 0;
    }

    async function runSimulation() {
        const parts = simulationParts();
        if (!parts || !parts.select || !parts.select.value) return;
        if (simulationInFlight) {
            // A change made while a run is under way is run straight after it, never alongside it.
            simulationRerun = true;
            return;
        }
        simulationInFlight = true;
        parts.run.disabled = true;
        parts.results.hidden = true;
        const namespace = parts.select.value;
        setSimulationStatus(parts, `Simulating on namespace ${namespace}…`, false);
        try {
            const payload = await api().fetchJson(
                `/api/deployments/chaos/benchmarks?source=live&namespace=${encodeURIComponent(namespace)}`
            );
            const metrics = payload.aggregate_metrics || {};
            if (metrics.topology_source !== "live") {
                setSimulationStatus(parts, "The server did not report a live topology, so these results are not shown as your cluster's.", true);
                return;
            }
            renderSimulationRows(parts, payload);
            const total = Math.round(metrics.total_scenarios || 0);
            const done = Math.round(metrics.completed_count || 0);
            setSimulationStatus(
                parts,
                `Namespace ${namespace}: ${done} of ${total} scenarios completed · mean D_rate ${pct(metrics.mean_d_rate)} · ` +
                `max ${pct(metrics.max_d_rate)}. Simulated on the cluster's real topology; not experimental evidence.`,
                false
            );
        } catch (error) {
            // The server's own message names the cause: no cluster connected, no workloads in this namespace, ...
            const hint = error && error.code === "TOPOLOGY_HAS_NO_WORKLOADS" ? " Choose another namespace." : "";
            setSimulationStatus(parts, `Could not simulate: ${error.message || "request failed"}.${hint}`, true);
        } finally {
            simulationInFlight = false;
            parts.run.disabled = !parts.select.value;
            if (simulationRerun) {
                simulationRerun = false;
                runSimulation();
            }
        }
    }

    async function loadSimulationNamespaces() {
        const parts = simulationParts();
        if (!parts || !parts.select) return;
        try {
            const overview = await api().fetchJson("/api/deployments/discovery/all");
            const rows = overview.namespaces || [];
            parts.select.replaceChildren();
            rows.forEach((row) => {
                const option = document.createElement("option");
                option.value = row.namespace;
                option.textContent = `${row.namespace} · ${(row.graph?.services || []).length} services`;
                parts.select.appendChild(option);
            });
            const chosen = chooseNamespace(rows);
            if (!rows.length || !chosen) {
                parts.select.replaceChildren();
                const none = document.createElement("option");
                none.value = "";
                none.textContent = "No namespace with workloads";
                parts.select.appendChild(none);
                parts.select.disabled = true;
                parts.run.disabled = true;
                setSimulationStatus(parts, "The connected cluster has no namespace with workloads to simulate.", false);
                return;
            }
            parts.select.value = chosen;
            parts.select.disabled = false;
            parts.run.disabled = false;
            setSimulationStatus(parts, "", false);
            await runSimulation();
        } catch (error) {
            parts.select.replaceChildren();
            const failed = document.createElement("option");
            failed.value = "";
            failed.textContent = "Cluster not available";
            parts.select.appendChild(failed);
            parts.select.disabled = true;
            parts.run.disabled = true;
            setSimulationStatus(parts, `Could not read the cluster: ${error.message || "request failed"}.`, true);
        }
    }

    function initSimulation() {
        const parts = simulationParts();
        if (!parts || !parts.select || !parts.run) return;
        parts.run.addEventListener("click", runSimulation);
        parts.select.addEventListener("change", runSimulation);
        loadSimulationNamespaces().catch((err) => console.warn("Failed to start the cluster simulation:", err));
    }

    function init() {
        load().catch((err) => console.warn("Failed to load live experiment results:", err));
        initSimulation();
    }

    return {
        init: init, load: load, buildFindings: buildFindings,
        runSimulation: runSimulation, loadSimulationNamespaces: loadSimulationNamespaces,
    };
})();
