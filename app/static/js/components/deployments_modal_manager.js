/**
 * Phase 5 Modal Manager:
 * Coordinates the Failure-Domain Violation Drill-Down and Snapshot Diff Modals.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsModalManager = (function () {
    "use strict";

    async function openViolationModal(serviceName) {
        const backdrop = document.getElementById("modal-violation-backdrop");
        if (!backdrop) return;

        // Reset
        document.getElementById("violation-service-name").textContent = serviceName;
        document.getElementById("violation-exposure-level").textContent = "Loading...";

        backdrop.removeAttribute("hidden");

        try {
            const resp = await fetch(`/api/deployments/drift/exposure/${serviceName}`);
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

    function openDiffModal() {
        const backdrop = document.getElementById("modal-diff-backdrop");
        if (!backdrop) return;
        backdrop.removeAttribute("hidden");
        executeSnapshotDiff().catch((err) => console.warn("Snapshot diff error:", err));
    }

    function closeDiffModal() {
        const backdrop = document.getElementById("modal-diff-backdrop");
        if (backdrop) backdrop.setAttribute("hidden", "true");
    }

    async function executeSnapshotDiff() {
        const scenarioSelect = document.getElementById("select-diff-scenario");
        const scenarioId = scenarioSelect ? scenarioSelect.value : "07_node_drain_eviction";

        const tbody = document.getElementById("diff-table-body");
        if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Comparing snapshots...</td></tr>';

        try {
            // The scenario result carries its own T0/T1 hashes, D_rate and pod moves.
            const resp = await fetch(`/api/deployments/chaos/run/${scenarioId}`, { method: "POST" });

            if (!resp.ok) {
                if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; color: var(--color-danger);">Diff failed for scenario.</td></tr>';
                return;
            }

            const diffData = await resp.json();
            renderDiffResults(diffData);
        } catch (err) {
            console.warn("Snapshot diff error:", err);
            if (tbody) tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">Diff execution error.</td></tr>';
        }
    }

    function renderDiffResults(data) {
        const dRateEl = document.getElementById("diff-d-rate-val");
        const deltaEEl = document.getElementById("diff-delta-e-val");
        const t0HashEl = document.getElementById("diff-t0-hash");
        const t1HashEl = document.getElementById("diff-t1-hash");

        const pct = (v) => (v == null ? "n/a" : `${(v * 100).toFixed(2)}%`);
        if (dRateEl) dRateEl.textContent = pct(data.d_rate);
        if (deltaEEl) deltaEEl.textContent = pct(data.delta_e_rate);
        if (t0HashEl && data.baseline_hash) t0HashEl.textContent = data.baseline_hash.substring(0, 12) + "...";
        if (t1HashEl && data.target_hash) t1HashEl.textContent = data.target_hash.substring(0, 12) + "...";

        const tbody = document.getElementById("diff-table-body");
        if (!tbody) return;

        tbody.innerHTML = "";
        const moves = data.transitions || [];
        const changed = data.drifted_services || [];
        if (moves.length === 0) {
            tbody.innerHTML = `<tr><td colspan="5" style="text-align: center;">No pod changed node (pods matched by name).` +
                `${changed.length ? " Exposure status changed for: " + changed.join(", ") : ""}</td></tr>`;
            return;
        }

        moves.forEach((t) => {
            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><code>${t.pod_count} pod(s)</code></td>
                <td><strong>${(t.services_affected || []).join(", ") || "-"}</strong></td>
                <td>${t.from_node}</td>
                <td>${t.to_node}</td>
                <td class="status-tag--drifted">moved</td>
            `;
            tbody.appendChild(tr);
        });
    }

    function init() {
        // Modal 1 controls
        const closeVioBtn = document.getElementById("btn-close-violation-modal");
        const disVioBtn = document.getElementById("btn-dismiss-violation-modal");
        if (closeVioBtn) closeVioBtn.addEventListener("click", closeViolationModal);
        if (disVioBtn) disVioBtn.addEventListener("click", closeViolationModal);

        // Modal 2 controls
        const openDiffBtn = document.getElementById("btn-open-diff-modal");
        const closeDiffBtn = document.getElementById("btn-close-diff-modal");
        const disDiffBtn = document.getElementById("btn-dismiss-diff-modal");
        const execDiffBtn = document.getElementById("btn-execute-snapshot-diff");

        if (openDiffBtn) openDiffBtn.addEventListener("click", openDiffModal);
        if (closeDiffBtn) closeDiffBtn.addEventListener("click", closeDiffModal);
        if (disDiffBtn) disDiffBtn.addEventListener("click", closeDiffModal);
        if (execDiffBtn) execDiffBtn.addEventListener("click", executeSnapshotDiff);

        // Global ESC handler
        document.addEventListener("keydown", (e) => {
            if (e.key === "Escape") {
                closeViolationModal();
                closeDiffModal();
            }
        });
    }

    return {
        init: init,
        openViolationModal: openViolationModal,
        closeViolationModal: closeViolationModal,
        openDiffModal: openDiffModal,
        closeDiffModal: closeDiffModal,
        executeSnapshotDiff: executeSnapshotDiff,
    };
})();
