/**
 * Phase 5 Resilience Drift & Signals Widget:
 * Renders categories counters, SPOF lists, and worker node risk rankings.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsDriftWidget = (function () {
    "use strict";

    // Every request carries the namespace chosen in the page header (deployments_namespace.js).
    const scoped = (url) => (window.DeploymentsNamespace ? window.DeploymentsNamespace.withNamespace(url) : url);

    // The page reads the connected cluster only, so drift always compares declared intent with that cluster.
    async function loadDriftSignals() {
        try {
            // 1. Categories Breakdown
            const catResp = await fetch(scoped("/api/deployments/drift/categories"));
            if (!catResp.ok) {
                const body = await catResp.json().catch(() => ({}));
                const container = document.getElementById("spof-workloads-list");
                const reason = body.detail?.message || body.detail || "HTTP " + catResp.status;
                if (container) {
                    container.textContent = `Drift analysis unavailable: ${reason}`;
                }
                return;
            }
            const catData = await catResp.json();
            renderCategoryCounters(catData.summary || {});

            // 2. SPOF Workloads
            const spofResp = await fetch(scoped("/api/deployments/drift/spofs"));
            if (spofResp.ok) {
                const spofs = await spofResp.json();
                renderSpofList(spofs);
            }

            // 3. Worker Node Risks
            const nodeResp = await fetch(scoped("/api/deployments/drift/nodes/risk"));
            if (nodeResp.ok) {
                const nodeRisks = await nodeResp.json();
                renderNodeRisks(nodeRisks);
            }
        } catch (err) {
            console.warn("Could not load drift signals:", err);
        }
    }

    function renderCategoryCounters(summary) {
        const scaleEl = document.getElementById("count-scale-drift");
        const affEl = document.getElementById("count-affinity-drift");
        const resEl = document.getElementById("count-resource-drift");
        const contEl = document.getElementById("count-contention-drift");
        const spreadEl = document.getElementById("count-spread-drift");

        if (scaleEl) scaleEl.textContent = summary.scale_drift ?? 0;
        if (affEl) affEl.textContent = summary.node_affinity_drift ?? 0;
        if (resEl) resEl.textContent = summary.resource_limit_drift ?? 0;
        if (contEl) contEl.textContent = summary.resource_contention ?? 0;
        if (spreadEl) spreadEl.textContent = summary.topology_spread_violations ?? 0;
    }

    function renderSpofList(spofs) {
        const container = document.getElementById("spof-workloads-list");
        const bannerCount = document.getElementById("spof-banner-count");
        const totalBadge = document.getElementById("spof-total-badge");

        if (bannerCount) bannerCount.textContent = spofs.length;
        if (totalBadge) totalBadge.textContent = `${spofs.length} exposed`;
        // Red only when something is exposed; 0 exposed is the good outcome.
        document.getElementById("spof-banner-badge")?.classList.toggle("spof-alert-badge--clear", spofs.length === 0);
        if (!container) return;

        container.innerHTML = "";
        if (spofs.length === 0) return;  // the spread map above already shows every service as safe

        spofs.forEach((s) => {
            const item = document.createElement("div");
            item.className = "spof-item";
            item.innerHTML = `
                <div>
                    <strong>${s.service_name}</strong>
                    <div style="font-size: 11px; color: var(--color-text-muted); margin-top: 2px;">
                        ${s.reason || "All replicas co-located on single failure domain."}
                    </div>
                </div>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <span class="status-badge status-badge--danger">${s.ready_replicas ?? 0} ready on ${(s.hosting_nodes || []).join(", ") || "n/a"}</span>
                    <button type="button" class="spof-drilldown-btn" data-spof-svc="${s.service_name}">
                        <span>Drill Down</span>
                        <span class="btn-arrow">→</span>
                    </button>
                </div>
            `;
            const drillBtn = item.querySelector("button");
            drillBtn.addEventListener("click", () => {
                if (window.DeploymentsModalManager) {
                    window.DeploymentsModalManager.openViolationModal(s.service_name);
                }
            });
            container.appendChild(item);
        });
    }

    // -----------------------------------------------------------------------------------------------
    // Spread map: services × worker nodes, one dot per pod. A row whose ready dots all sit in one
    // column is exposed. The status is the graph's own (exposedFailureDomains), not recomputed here.
    // -----------------------------------------------------------------------------------------------

    const PENDING = "__pending__";
    const shortNode = (node) => node.replace(/^.*-(worker\d*|control-plane)$/, "$1");

    function spreadMatrix(deployment) {
        const services = deployment.services || [];
        const nodes = (deployment.nodes || []).map((n) => n.name);
        const rows = services.map((svc) => {
            const cells = {};
            (svc.pods || []).forEach((pod) => {
                const key = pod.node || PENDING;
                const cell = (cells[key] = cells[key] || { ready: 0, notReady: 0 });
                if (pod.ready) cell.ready++;
                else cell.notReady++;
            });
            const ready = Object.values(cells).reduce((n, c) => n + c.ready, 0);
            const exposedOn = svc.exposedFailureDomains ?? svc.exposed_failure_domains ?? [];
            let status = "safe";
            if (ready === 0) status = "unavailable";
            else if (exposedOn.length) status = "exposed";
            return { name: svc.name, cells, ready, status, exposedOn };
        });
        // Pods on a node the graph does not list (or on none) still get a column, so no pod disappears.
        const extra = new Set();
        rows.forEach((r) => Object.keys(r.cells).forEach((k) => { if (!nodes.includes(k)) extra.add(k); }));
        const columns = [...nodes, ...[...extra].sort((a, b) => (a === PENDING) - (b === PENDING) || a.localeCompare(b))];
        const order = { exposed: 0, unavailable: 1, safe: 2 };
        rows.sort((a, b) => order[a.status] - order[b.status] || a.name.localeCompare(b.name));
        return { columns, rows };
    }

    const STATUS_TEXT = { safe: "✅ Safe", exposed: "⚠️ Exposed", unavailable: "⛔ No ready replica" };
    const STATUS_VARIANT = { safe: "success", exposed: "danger", unavailable: "muted" };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text != null) node.textContent = text;
        return node;
    }

    function renderSpreadMap(deployment) {
        const table = document.querySelector("[data-role='spread-map']");
        if (!table) return;
        const { columns, rows } = spreadMatrix(deployment);
        const head = el("tr");
        head.appendChild(el("th", null, "Service"));
        columns.forEach((c) => {
            const th = el("th", "spread-map__node", c === PENDING ? "⏳ Pending" : `🖥️ ${shortNode(c)}`);
            th.title = c === PENDING ? "Pods with no node yet" : c;
            head.appendChild(th);
        });
        head.appendChild(el("th", null, "If one node fails"));
        const body = rows.map((row) => {
            const tr = el("tr", `spread-map__row spread-map__row--${row.status}`);
            tr.appendChild(el("th", "spread-map__service", row.name)).scope = "row";
            columns.forEach((c) => {
                const cell = row.cells[c] || { ready: 0, notReady: 0 };
                const td = el("td", "spread-map__cell");
                for (let i = 0; i < cell.ready; i++) td.appendChild(el("span", "spread-dot"));
                for (let i = 0; i < cell.notReady; i++) td.appendChild(el("span", "spread-dot spread-dot--notready"));
                td.title = `${row.name} on ${c === PENDING ? "no node" : c}: ${cell.ready} ready, ${cell.notReady} not ready`;
                tr.appendChild(td);
            });
            const verdict = el("td");
            const badge = el("span", `status-badge status-badge--no-dot status-badge--${STATUS_VARIANT[row.status]}`, STATUS_TEXT[row.status]);
            if (row.status === "exposed") badge.title = `Goes down if ${row.exposedOn.join(" or ")} fails`;
            verdict.appendChild(badge);
            tr.appendChild(verdict);
            return tr;
        });
        table.querySelector("thead").replaceChildren(head);
        table.querySelector("tbody").replaceChildren(...body);
    }

    const zonePrefix = (zone) => (zone ? "Zone: " + zone + " · " : "");

    function renderNodeRisks(nodeRisks) {
        const container = document.getElementById("node-risk-rankings-list");
        if (!container) return;

        container.innerHTML = "";
        nodeRisks.forEach((n) => {
            const item = document.createElement("div");
            const riskClass = n.risk_level === "CRITICAL"
                ? "node-risk-item--critical"
                : "node-risk-item--balanced";

            item.className = `node-risk-item ${riskClass}`;
            item.innerHTML = `
                <div>
                    <strong>🖥️ ${n.node_name}</strong>
                    <div style="font-size: 11px; color: var(--color-text-muted); margin-top: 2px;">
                        ${zonePrefix(n.zone)}${n.pod_count || 0} pods · only node hosting ${n.exposed_service_count || 0} service(s)
                    </div>
                </div>
                <span class="status-badge ${n.risk_level === 'CRITICAL' ? 'status-badge--danger' : 'status-badge--success'}">
                    ${n.exposed_service_count > 0 ? (n.exposed_services || []).join(", ") : "No exposed services"}
                </span>
            `;
            container.appendChild(item);
        });
    }

    function init() {
        // Loaded by deployments.js once the data source is known.
    }

    return {
        init: init,
        loadDriftSignals: loadDriftSignals,
        renderSpreadMap: renderSpreadMap,
        spreadMatrix: spreadMatrix,
    };
})();
