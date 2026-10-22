/**
 * Phase 5 Declared Intent vs Live Compliance Widget:
 * Renders the side-by-side specification comparison table.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsIntentWidget = (function () {
    "use strict";

    // Every request carries the namespace chosen in the page header (deployments_namespace.js).
    const scoped = (url) => (window.DeploymentsNamespace ? window.DeploymentsNamespace.withNamespace(url) : url);

    // Same order as service._declared_target: say where the declared intent comes from.
    async function showManifestSource() {
        const note = document.querySelector("[data-role='manifest-source']");
        if (!note) return;
        try {
            const resp = await fetch("/api/deployments/manifests/source");
            if (!resp.ok) return;
            const src = await resp.json();
            note.classList.toggle("manifest-source--warning", !src.found);
            if (src.source === "cluster") {
                note.textContent = "Declared intent from the Deployment specs of the connected cluster (no manifest file found).";
            } else if (src.found) {
                note.textContent = `Declared intent from ${src.path}.`;
            } else {
                note.textContent = `The named manifest ${src.path} does not exist, so there is no declared intent to compare.`;
            }
        } catch (err) {
            console.warn("Could not load manifest source:", err);
        }
    }

    // activeServices are the connected cluster's services; the declared intent is compared with them.
    async function loadIntentCompliance(activeServices) {
        await showManifestSource();
        try {
            const resp = await fetch(scoped("/api/deployments/manifests"));
            if (!resp.ok) return;
            const intents = await resp.json();
            renderIntentTable(intents, activeServices);
        } catch (err) {
            console.warn("Could not load manifest intent:", err);
        }
        await loadPlacementCompliance();
    }

    // Placement-rule compliance comes from the backend definition (placement_metrics.compliance_rate).
    async function loadPlacementCompliance() {
        const rateEl = document.getElementById("compliance-rate-val");
        if (!rateEl) return;
        rateEl.textContent = "n/a";
        try {
            const resp = await fetch(scoped("/api/deployments/manifests/compliance"));
            if (!resp.ok) return;
            const data = await resp.json();
            rateEl.textContent = data.constraint_compliance_rate == null
                ? "n/a (no service declares a rule)"
                : `${(data.constraint_compliance_rate * 100).toFixed(1)}% of ${data.services_with_placement_rules}`;
        } catch (err) {
            console.warn("Could not load placement-rule compliance:", err);
        }
    }

    // Live ready replicas from camelCase, snake_case or the pod list; null when the service was not observed.
    function liveReadyReplicas(live) {
        if (live.readyReplicas !== undefined) return live.readyReplicas;
        if (live.ready_replicas !== undefined) return live.ready_replicas;
        if (Array.isArray(live.pods)) return live.pods.filter((p) => p.ready).length;
        return null;
    }

    function limitsText(item) {
        if (!item.resource_limits) return "None";
        return `${item.resource_limits.cpu || "N/A"} / ${item.resource_limits.memory || "N/A"}`;
    }

    function replicaStatusHtml(comparable, replicaMismatch, declaredRep, readyRep) {
        if (replicaMismatch) {
            return `<span class="status-pill status-pill--warning" title="Declared ${declaredRep} vs live ${readyRep} ready">⚠️ Replica drift</span>`;
        }
        if (comparable) return '<span class="status-pill status-pill--success">✓ Match</span>';
        return '<span class="status-pill">Not observed</span>';
    }

    // Keyed by namespace + name, like the backend, so a YAML in another namespace that reuses a
    // service name (e.g. deploy/test-workloads in resilience-test) is never compared with it.
    const key = (namespace, name) => `${namespace || "default"}/${name}`;

    // A declared Deployment is matched to the live Service that the backend resolved it to through owner
    // references (deploymentNames), not only by name: Bookinfo's Deployment reviews-v1 backs Service reviews.
    function buildLiveMap(liveServices) {
        const liveMap = {};
        (liveServices || []).forEach((s) => {
            liveMap[key(s.namespace, s.name)] = s;
            (s.deploymentNames ?? s.deployment_names ?? []).forEach((deployment) => {
                liveMap[key(s.namespace, deployment)] = s;
            });
        });
        return liveMap;
    }

    function renderIntentTable(intents, liveServices = []) {
        const tbody = document.getElementById("intent-table-body");
        if (!tbody) return;

        tbody.innerHTML = "";
        const liveMap = buildLiveMap(liveServices);

        let compliantCount = 0;
        let comparedCount = 0;

        intents.forEach((item) => {
            const svcName = item.service_name || "unknown";
            const declaredRep = item.expected_replicas ?? item.declared_replicas ?? null;
            const readyRep = liveReadyReplicas(liveMap[key(item.namespace, svcName)] || {});
            const limits = limitsText(item);
            const hasAffinity = item.has_node_affinity ? "✓ Required" : "None";
            const hasSpread = item.spread_constraints?.length > 0 ? "✓ Configured" : "None";

            // Only compared when both sides are known; otherwise shown as not observed.
            const comparable = declaredRep != null && readyRep != null;
            const replicaMismatch = comparable && readyRep !== declaredRep;
            if (comparable) {
                comparedCount++;
                if (!replicaMismatch) compliantCount++;
            }
            const statusHtml = replicaStatusHtml(comparable, replicaMismatch, declaredRep, readyRep);

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><strong>${svcName}</strong></td>
                <td>${declaredRep ?? "n/a"}</td>
                <td><strong style="color: ${replicaMismatch ? 'var(--color-danger, #ef4444)' : 'inherit'};">${readyRep ?? "n/a"}</strong></td>
                <td>${statusHtml}</td>
                <td class="intent-col--extra"><code>${limits}</code></td>
                <td class="intent-col--extra">${hasAffinity}</td>
                <td class="intent-col--extra">${hasSpread}</td>
            `;
            tbody.appendChild(tr);
        });

        const matchEl = document.getElementById("replica-match-val");
        if (matchEl) {
            matchEl.textContent = comparedCount > 0 ? `${compliantCount} / ${comparedCount}` : "n/a";
        }
    }

    // The limits and placement-rule columns are shown only while "Show details" is open.
    function init() {
        const details = document.querySelector("[data-role='intent-details']");
        const table = document.querySelector("[data-role='intent-table']");
        if (!details || !table) return;
        details.addEventListener("toggle", () => table.classList.toggle("intent-table--all", details.open));
    }

    return {
        init: init,
        loadIntentCompliance: loadIntentCompliance,
    };
})();
