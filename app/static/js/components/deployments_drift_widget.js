/**
 * Phase 5 Resilience Drift & Signals Widget:
 * Renders categories counters, SPOF lists, and worker node risk rankings.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsDriftWidget = (function () {
    "use strict";

    async function loadDriftSignals() {
        try {
            // 1. Categories Breakdown
            const catResp = await fetch("/api/deployments/drift/categories");
            if (catResp.ok) {
                const catData = await catResp.json();
                renderCategoryCounters(catData.summary || {});
            }

            // 2. SPOF Workloads
            const spofResp = await fetch("/api/deployments/drift/spofs");
            if (spofResp.ok) {
                const spofs = await spofResp.json();
                renderSpofList(spofs);
            }

            // 3. Worker Node Risks
            const nodeResp = await fetch("/api/deployments/drift/nodes/risk");
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
        if (totalBadge) totalBadge.textContent = `${spofs.length} SPOFs`;
        if (!container) return;

        container.innerHTML = "";
        if (spofs.length === 0) {
            container.innerHTML = '<span class="node-card__empty">No single points of failure detected.</span>';
            return;
        }

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
                    <span class="status-badge status-badge--danger">${Math.round((s.replica_concentration || 1.0) * 100)}% Concentration</span>
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

    function renderNodeRisks(nodeRisks) {
        const container = document.getElementById("node-risk-rankings-list");
        if (!container) return;

        container.innerHTML = "";
        nodeRisks.forEach((n) => {
            const item = document.createElement("div");
            const riskClass = n.risk_level === "CRITICAL"
                ? "node-risk-item--critical"
                : (n.risk_level === "ELEVATED" ? "node-risk-item--elevated" : "node-risk-item--balanced");

            item.className = `node-risk-item ${riskClass}`;
            item.innerHTML = `
                <div>
                    <strong>🖥️ ${n.node_name}</strong>
                    <div style="font-size: 11px; color: var(--color-text-muted); margin-top: 2px;">
                        Zone: ${n.zone || "default"} · ${n.pod_count || 0} Pods hosted
                    </div>
                </div>
                <span class="status-badge ${n.risk_level === 'CRITICAL' ? 'status-badge--danger' : 'status-badge--success'}">
                    ${n.risk_level}
                </span>
            `;
            container.appendChild(item);
        });
    }

    function init() {
        loadDriftSignals();
    }

    return {
        init: init,
        loadDriftSignals: loadDriftSignals,
    };
})();
