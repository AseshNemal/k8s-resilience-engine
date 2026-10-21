/**
 * Phase 5 Modal Manager:
 * Opens the per-service exposure drill-down.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsModalManager = (function () {
    "use strict";

    // Every request carries the namespace chosen in the page header (deployments_namespace.js).
    const scoped = (url) => (window.DeploymentsNamespace ? window.DeploymentsNamespace.withNamespace(url) : url);

    async function openViolationModal(serviceName) {
        const backdrop = document.getElementById("modal-violation-backdrop");
        if (!backdrop) return;

        // Reset
        document.getElementById("violation-service-name").textContent = serviceName;
        document.getElementById("violation-exposure-level").textContent = "Loading...";

        backdrop.removeAttribute("hidden");

        try {
            const resp = await fetch(scoped(`/api/deployments/drift/exposure/${encodeURIComponent(serviceName)}`));
            if (!resp.ok) return;
            const exp = await resp.json();

            document.getElementById("violation-exposure-level").textContent = exp.exposure_level ?? "Unknown";
            document.getElementById("violation-concentration-val").textContent =
                exp.replica_concentration == null
                    ? "Concentration n/a (no ready replicas)"
                    : `${Math.round(exp.replica_concentration * 100)}% of ready replicas on one node`;
            document.getElementById("violation-severity-score").textContent =
                `${exp.ready_replicas ?? 0} ready replica(s)`;

            const nodesList = document.getElementById("violation-hosting-nodes");
            if (nodesList) {
                const domains = exp.exposed_failure_domains || [];
                nodesList.innerHTML = domains.length > 0
                    ? domains.map((n) => `<li>🖥️ <strong>${n}</strong> (losing this node leaves 0 ready replicas)</li>`).join("")
                    : "<li>No single node removes all ready replicas.</li>";
            }

            const rulesList = document.getElementById("violation-rules-list");
            if (rulesList) {
                rulesList.innerHTML = (exp.violations && exp.violations.length > 0)
                    ? exp.violations.map((v) => `<div class="violation-chip">⚠️ ${v.description}</div>`).join("")
                    : '<div style="font-size: 12px; color: var(--color-text-muted);">No declared-intent violations detected.</div>';
            }
        } catch (e) {
            console.warn("Could not fetch service exposure detail:", e);
        }
    }

    function closeViolationModal() {
        const backdrop = document.getElementById("modal-violation-backdrop");
        if (backdrop) backdrop.setAttribute("hidden", "true");
    }

    function init() {
        const closeVioBtn = document.getElementById("btn-close-violation-modal");
        const disVioBtn = document.getElementById("btn-dismiss-violation-modal");
        if (closeVioBtn) closeVioBtn.addEventListener("click", closeViolationModal);
        if (disVioBtn) disVioBtn.addEventListener("click", closeViolationModal);

        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") closeViolationModal();
        });
    }

    return {
        init: init,
        openViolationModal: openViolationModal,
        closeViolationModal: closeViolationModal,
    };
})();
