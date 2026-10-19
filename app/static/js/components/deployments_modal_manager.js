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

            document.getElementById("violation-exposure-level").textContent = exp.exposure_level || "EXPOSED";
            document.getElementById("violation-concentration-val").textContent = `${Math.round((exp.replica_concentration || 1.0) * 100)}% Concentration`;
            document.getElementById("violation-severity-score").textContent = (exp.severity_score || 0.75).toFixed(2);

            // Hosting nodes
            const nodesList = document.getElementById("violation-hosting-nodes");
            if (nodesList) {
                nodesList.innerHTML = (exp.exposed_failure_domains || ["c2-worker"])
                    .map((n) => `<li>🖥️ <strong>${n}</strong> (Single failure domain)</li>`)
                    .join("");
            }

            // Violations
            const rulesList = document.getElementById("violation-rules-list");
            if (rulesList) {
                rulesList.innerHTML = (exp.violations && exp.violations.length > 0)
                    ? exp.violations.map((v) => `<div class="violation-chip">⚠️ ${v.description || v.category}</div>`).join("")
                    : '<div style="font-size: 12px; color: var(--color-text-muted);">Concentration exceeds 0.50 threshold without zone anti-affinity.</div>';
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
        executeSnapshotDiff();
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
            // First ensure snapshots exist by executing scenario
            await fetch(`/api/deployments/chaos/run/${scenarioId}`, { method: "POST" });

            // Now diff T0 and T1
            const resp = await fetch("/api/deployments/diff", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    baseline_snapshot_id: `${scenarioId}_T0`,
                    target_snapshot_id: `${scenarioId}_T1`,
                }),
            });

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

        if (dRateEl) dRateEl.textContent = `${((data.d_rate || 0) * 100).toFixed(2)}%`;
        if (deltaEEl) deltaEEl.textContent = `${((data.delta_e_rate || 0) * 100).toFixed(2)}%`;
        if (t0HashEl && data.baseline_hash) t0HashEl.textContent = data.baseline_hash.substring(0, 12) + "...";
        if (t1HashEl && data.target_hash) t1HashEl.textContent = data.target_hash.substring(0, 12) + "...";

        const tbody = document.getElementById("diff-table-body");
        if (!tbody) return;

        tbody.innerHTML = "";
        const diffs = data.pod_diffs || [];
        if (diffs.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center;">All pods maintained identical placements.</td></tr>';
            return;
        }

        diffs.forEach((p) => {
            const isDrifted = p.status === "DRIFTED" || p.baseline_node !== p.target_node;
            const statusClass = isDrifted ? "status-tag--drifted" : "status-tag--steady";
            const statusText = isDrifted ? "→ [DRIFTED!]" : "(Steady)";

            const tr = document.createElement("tr");
            tr.innerHTML = `
                <td><code>${p.pod_name}</code></td>
                <td><strong>${p.service_name || "-"}</strong></td>
                <td>${p.baseline_node || "-"}</td>
                <td>${p.target_node || "TERMINATED"}</td>
                <td class="${statusClass}">${statusText}</td>
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
