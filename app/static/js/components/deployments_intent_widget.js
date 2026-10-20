/**
 * Phase 5 Declared Intent vs Live Compliance Widget:
 * Renders the side-by-side specification comparison table.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsIntentWidget = (function () {
    "use strict";

    async function loadIntentCompliance(activeServices) {
        try {
            const resp = await fetch("/api/deployments/manifests");
            if (!resp.ok) return;
            const intents = await resp.json();

            renderIntentTable(intents, activeServices);
        } catch (err) {
            console.warn("Could not load manifest intent:", err);
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

        intents.forEach((item) => {
            const svcName = item.service_name || "unknown";
            const live = liveMap[svcName] || {};
            const declaredRep = item.expected_replicas ?? item.declared_replicas ?? 1;

            // Extract live ready replicas from snake_case or camelCase or pod list
            let readyRep = declaredRep;
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

            // Drift is detected if replica count doesn't match OR if drift is flagged on live service
            const replicaMismatch = (readyRep !== declaredRep);
            const flaggedDrift = Boolean(live.driftDetected || live.drift_detected);
            const hasDrift = replicaMismatch || flaggedDrift;

            const isCompliant = !hasDrift;
            if (isCompliant) {
                compliantCount++;
            }

            const driftTitle = replicaMismatch
                ? "Replica Drift: Declared " + declaredRep + " vs Live " + readyRep
                : "Resilience Drift Detected";
            let statusHtml = '<span class="status-pill status-pill--success">✓ Compliant</span>';
            if (!isCompliant) {
                statusHtml = `<span class="status-pill status-pill--warning" title="${driftTitle}">⚠️ Drifted</span>`;
            }

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${svcName}</strong></td>
                <td>${declaredRep}</td>
                <td><strong style="color: ${replicaMismatch ? 'var(--color-danger, #ef4444)' : 'inherit'};">${readyRep}</strong></td>
                <td><code>${limits}</code></td>
                <td>${hasAffinity}</td>
                <td>${hasSpread}</td>
                <td>${statusHtml}</td>
            `;
            tbody.appendChild(tr);
        });

        // Dynamically update the overall compliance rate based on the actual table rows
        const rateEl = document.getElementById("compliance-rate-val");
        if (rateEl && intents.length > 0) {
            const compRate = (compliantCount / intents.length) * 100;
            rateEl.textContent = `${compRate.toFixed(1)}%`;
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
