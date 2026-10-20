/**
 * Phase 5 Declared Intent vs Live Compliance Widget:
 * Renders the side-by-side specification comparison table.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsIntentWidget = (function () {
    "use strict";

    async function loadIntentCompliance(activeServices, isLive) {
        try {
            const resp = await fetch("/api/deployments/manifests");
            if (!resp.ok) return;
            const intents = await resp.json();

            // The manifests are the real YAML; comparing them with the sample fixture would invent drift.
            renderIntentTable(intents, isLive ? activeServices : []);
        } catch (err) {
            console.warn("Could not load manifest intent:", err);
        }
        await loadPlacementCompliance(isLive);
    }

    // Placement-rule compliance comes from the backend definition (placement_metrics.compliance_rate)
    // and needs a live cluster; on sample data it stays n/a.
    async function loadPlacementCompliance(isLive) {
        const rateEl = document.getElementById("compliance-rate-val");
        if (!rateEl) return;
        rateEl.textContent = "n/a";
        rateEl.title = isLive ? "" : "Live cluster only";
        if (!isLive) return;
        try {
            const resp = await fetch("/api/deployments/manifests/compliance");
            if (!resp.ok) return;
            const data = await resp.json();
            rateEl.textContent = data.constraint_compliance_rate == null
                ? "n/a (no service declares a rule)"
                : `${(data.constraint_compliance_rate * 100).toFixed(1)}% of ${data.services_with_placement_rules}`;
        } catch (err) {
            console.warn("Could not load placement-rule compliance:", err);
        }
    }

    function renderIntentTable(intents, liveServices = []) {
        const tbody = document.getElementById("intent-table-body");
        if (!tbody) return;

        tbody.innerHTML = "";
        const liveMap = {};
        (liveServices || []).forEach((s) => {
            liveMap[s.name] = s;
        });

        let compliantCount = 0;
        let comparedCount = 0;

        intents.forEach((item) => {
            const svcName = item.service_name || "unknown";
            const live = liveMap[svcName] || {};
            const declaredRep = item.expected_replicas ?? item.declared_replicas ?? null;

            // Live ready replicas from snake_case or camelCase or pod list; null when not observed
            let readyRep = null;
            if (live.readyReplicas !== undefined) {
                readyRep = live.readyReplicas;
            } else if (live.ready_replicas !== undefined) {
                readyRep = live.ready_replicas;
            } else if (live.pods && Array.isArray(live.pods)) {
                readyRep = live.pods.filter((p) => p.ready).length;
            }

            const limits = item.resource_limits
                ? `${item.resource_limits.cpu || "N/A"} / ${item.resource_limits.memory || "N/A"}`
                : "None";

            const hasAffinity = item.has_node_affinity ? "✓ Required" : "None";
            const hasSpread = (item.spread_constraints && item.spread_constraints.length > 0) ? "✓ Configured" : "None";

            // Only compared when both sides are known; otherwise shown as not observed.
            const comparable = declaredRep != null && readyRep != null;
            const replicaMismatch = comparable && readyRep !== declaredRep;
            if (comparable) {
                comparedCount++;
                if (!replicaMismatch) compliantCount++;
            }

            let statusHtml = '<span class="status-pill">Not observed</span>';
            if (replicaMismatch) {
                statusHtml = `<span class="status-pill status-pill--warning" title="Declared ${declaredRep} vs live ${readyRep} ready">⚠️ Replica drift</span>`;
            } else if (comparable) {
                statusHtml = '<span class="status-pill status-pill--success">✓ Match</span>';
            }

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${svcName}</strong></td>
                <td>${declaredRep ?? "n/a"}</td>
                <td><strong style="color: ${replicaMismatch ? 'var(--color-danger, #ef4444)' : 'inherit'};">${readyRep ?? "n/a"}</strong></td>
                <td><code>${limits}</code></td>
                <td>${hasAffinity}</td>
                <td>${hasSpread}</td>
                <td>${statusHtml}</td>
            `;
            tbody.appendChild(tr);
        });

        const matchEl = document.getElementById("replica-match-val");
        if (matchEl) {
            matchEl.textContent = comparedCount > 0 ? `${compliantCount} / ${comparedCount}` : "n/a";
        }
    }

    function init() {
        // Will be called with active services by deployments.js
    }

    return {
        init: init,
        loadIntentCompliance: loadIntentCompliance,
    };
})();
