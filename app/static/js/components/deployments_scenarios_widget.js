/**
 * Phase 5 Chaos Scenarios & Parametric Placement Drift Widget:
 * Executes chaos simulations, loads the 28-trial placement drift ensemble,
 * renders sensitivity breakdowns, and visualizes stochastic Markov pod migration flows.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsScenariosWidget = (function () {
    "use strict";

    let currentLatexCode = "";
    const warn = (what) => (err) => console.warn(`${what}:`, err);

    async function loadMarkovMatrix(scenarioId) {
        const container = document.getElementById("markov-matrix-container");
        if (!container) return;

        container.innerHTML = '<div class="loading-state">Loading Markov Transition Matrix…</div>';

        try {
            const resp = await fetch(`/api/deployments/chaos/matrix/${scenarioId}`);
            if (!resp.ok) {
                container.innerHTML = '<div class="ui-note">Transition matrix data not available for this scenario.</div>';
                return;
            }

            const data = await resp.json();
            currentLatexCode = data.latex_table || "";
            renderMatrixGrid(data);
        } catch (err) {
            console.warn("Failed to load transition matrix:", err);
            container.innerHTML = '<div class="ui-note">Error rendering matrix grid.</div>';
        }
    }

    function renderMatrixGrid(data) {
        const container = document.getElementById("markov-matrix-container");
        if (!container) return;

        const nodes = data.nodes || [];
        const matrix = data.transition_matrix || {};

        let tableHtml = '<table class="data-table data-table--compact" style="width: 100%;">';
        tableHtml += String.raw`<thead><tr><th style="background: var(--color-surface-soft);">FROM (T0) \ TO (T1)</th>`;
        nodes.forEach((n) => {
            tableHtml += `<th style="text-align: center; text-transform: uppercase;">${n}</th>`;
        });
        tableHtml += '<th style="text-align: center; color: var(--color-text-muted);">MIGRATIONS OUT</th></tr></thead><tbody>';

        nodes.forEach((src) => {
            let rowOut = 0;
            tableHtml += `<tr><td style="font-weight: 600; font-family: monospace;">${src}</td>`;
            nodes.forEach((dst) => {
                const count = matrix[src] ? matrix[src][dst] || 0 : 0;
                if (src === dst) {
                    tableHtml += `<td style="text-align: center; background: rgba(16, 185, 129, 0.08); color: var(--color-success); font-weight: 700;">
                        ${count > 0 ? count + ' <span style="font-size: 10px; font-weight: 400; display: block;">remained</span>' : '0'}
                    </td>`;
                } else if (count > 0) {
                    rowOut += count;
                    tableHtml += `<td style="text-align: center; background: rgba(239, 68, 68, 0.12); color: var(--color-danger); font-weight: 800;">
                        → ${count} <span style="font-size: 10px; font-weight: 400; display: block;">displaced</span>
                    </td>`;
                } else {
                    tableHtml += '<td style="text-align: center; color: var(--color-text-muted); opacity: 0.4;">—</td>';
                }
            });
            tableHtml += `<td style="text-align: center; font-weight: 700; color: ${rowOut > 0 ? 'var(--color-danger)' : 'var(--color-text-muted)'};">${rowOut > 0 ? '− ' + rowOut : '0'}</td></tr>`;
        });

        tableHtml += '</tbody></table>';

        tableHtml += `
            <div style="display: flex; gap: 1rem; margin-top: 1rem; font-size: 12px; background: var(--color-surface-soft); padding: 8px 12px; border-radius: 6px;">
                <span>Drift Rate ($D_{rate}$): <strong>${data.d_rate == null ? "n/a" : (data.d_rate * 100).toFixed(1) + "%"}</strong></span>
                <span>Exposure Rate Change ($\Delta E$): <strong>${data.delta_e_rate == null ? "n/a" : (data.delta_e_rate * 100).toFixed(1) + "%"}</strong></span>
                <span>Mode: <strong>${data.execution_mode || "DRY_RUN_SIMULATION"}</strong> (scripted placements)</span>
            </div>
        `;

        container.innerHTML = tableHtml;
    }

    function renderEnsembleSummary(container, ensemble) {
        if (!container || !ensemble) return;
        container.innerHTML = "";

        const pct = (v) => (v == null ? "n/a" : `${(v * 100).toFixed(1)}%`);
        const ci = ensemble.confidence_interval_95;

        const notice = document.createElement("div");
        notice.className = "ui-note";
        notice.textContent = ensemble.is_experimental_evidence
            ? "Live-cluster observations."
            : `⚠ ${ensemble.evidence_note}`;
        container.appendChild(notice);

        const items = [
            {
                label: "Trials",
                value: `${ensemble.measured_trials} / ${ensemble.total_trials}`,
                sub: `Measurable D_rate / executed (${ensemble.execution_mode})`,
            },
            {
                label: "Mean Drift Rate (μ)",
                value: pct(ensemble.mean_d_rate),
                sub: `Sample standard deviation s = ${pct(ensemble.std_dev_d_rate)}`,
            },
            {
                label: "95% Confidence Interval",
                value: ci ? `[${pct(ci[0])}, ${pct(ci[1])}]` : "n/a",
                sub: ensemble.ci_method,
                isCi: Boolean(ci),
                ciLow: ci ? ci[0] * 100 : 0,
                ciHigh: ci ? ci[1] * 100 : 0,
                mean: (ensemble.mean_d_rate ?? 0) * 100,
            },
            {
                label: "Mean Change in Exposure Rate (ΔE)",
                value: pct(ensemble.mean_delta_e_rate),
                sub: "E_rate(T1) − E_rate(T0), averaged",
            },
        ];

        items.forEach((item) => {
            const box = document.createElement("div");
            box.className = "ensemble-metric-box";

            const label = document.createElement("span");
            label.className = "ensemble-metric-box__label";
            label.textContent = item.label;

            const val = document.createElement("span");
            val.className = "ensemble-metric-box__value";
            val.textContent = item.value;

            const sub = document.createElement("span");
            sub.className = "ensemble-metric-box__sub";
            sub.textContent = item.sub;

            box.append(label, val, sub);

            if (item.isCi) {
                const ciBarContainer = document.createElement("div");
                ciBarContainer.className = "ci-visual-container";

                const ciTrack = document.createElement("div");
                ciTrack.className = "ci-visual-track";

                const ciBand = document.createElement("div");
                ciBand.className = "ci-visual-band";
                ciBand.style.left = `${Math.max(0, item.ciLow)}%`;
                ciBand.style.width = `${Math.max(4, Math.min(100, item.ciHigh - item.ciLow))}%`;

                const ciMeanMarker = document.createElement("div");
                ciMeanMarker.className = "ci-visual-mean";
                ciMeanMarker.style.left = `${item.mean}%`;

                ciTrack.append(ciBand, ciMeanMarker);

                const ciLabels = document.createElement("div");
                ciLabels.className = "ci-visual-labels";
                ciLabels.innerHTML = `<span>0%</span><span class="ci-badge">${item.ciLow}% — ${item.ciHigh}% (μ=${item.mean}%)</span><span>100%</span>`;

                ciBarContainer.append(ciTrack, ciLabels);
                box.appendChild(ciBarContainer);
            }

            container.appendChild(box);
        });
    }

    function renderSensitivityBreakdowns(root, ensemble) {
        if (!root || !ensemble) return;

        const svcTbody = root.querySelector("[data-role='ensemble-service-sensitivity-body']");
        const catTbody = root.querySelector("[data-role='ensemble-category-sensitivity-body']");

        // Label derived only from the measured mean D_rate of the row (no per-service presets).
        const rateLabel = (rate) => (rate > 0
            ? { label: "EXPOSURE CHANGED", variant: "warning" }
            : { label: "NO EXPOSURE CHANGE", variant: "success" });

        if (svcTbody && ensemble.per_service_drift) {
            svcTbody.innerHTML = "";
            const sortedServices = Object.entries(ensemble.per_service_drift).sort((a, b) => b[1] - a[1]);

            sortedServices.forEach(([svc, rate]) => {
                const tr = document.createElement("tr");

                const svcCell = document.createElement("td");
                const code = document.createElement("code");
                code.style.fontWeight = "600";
                code.textContent = svc;
                svcCell.appendChild(code);

                const countCell = document.createElement("td");
                const count = ensemble.trials ? ensemble.trials.filter((t) => t.target_service === svc).length : 0;
                countCell.textContent = `${count}`;

                const driftCell = document.createElement("td");
                const dWrapper = document.createElement("div");
                dWrapper.className = "drift-cell-wrapper";

                let fillClass = "green";
                if (rate >= 0.5) fillClass = "red";
                else if (rate >= 0.2) fillClass = "yellow";

                const bar = document.createElement("div");
                bar.className = "drift-progress-bar";
                const fill = document.createElement("div");
                fill.className = `drift-progress-fill drift-progress-fill--${fillClass}`;
                fill.style.width = `${Math.min(100, rate * 100)}%`;
                bar.appendChild(fill);

                const dText = document.createElement("span");
                dText.style.fontWeight = "600";
                dText.textContent = `${(rate * 100).toFixed(2)}%`;

                dWrapper.append(bar, dText);
                driftCell.appendChild(dWrapper);

                const tierCell = document.createElement("td");
                const tierInfo = rateLabel(rate);
                const badge = document.createElement("span");
                badge.className = `status-badge status-badge--${tierInfo.variant}`;
                badge.textContent = tierInfo.label;
                tierCell.appendChild(badge);

                tr.append(svcCell, countCell, driftCell, tierCell);
                svcTbody.appendChild(tr);
            });
        }

        if (catTbody && ensemble.per_disruption_drift) {
            catTbody.innerHTML = "";
            const sortedCategories = Object.entries(ensemble.per_disruption_drift).sort((a, b) => b[1] - a[1]);

            sortedCategories.forEach(([cat, rate]) => {
                const tr = document.createElement("tr");

                const catCell = document.createElement("td");
                catCell.style.fontWeight = "600";
                catCell.textContent = cat.replaceAll("_", " ");

                const countCell = document.createElement("td");
                const count = ensemble.trials ? ensemble.trials.filter((t) => t.scenario_category === cat).length : 0;
                countCell.textContent = `${count}`;

                const driftCell = document.createElement("td");
                const dWrapper = document.createElement("div");
                dWrapper.className = "drift-cell-wrapper";

                let fillClass = "green";
                if (rate >= 0.5) fillClass = "red";
                else if (rate >= 0.2) fillClass = "yellow";

                const bar = document.createElement("div");
                bar.className = "drift-progress-bar";
                const fill = document.createElement("div");
                fill.className = `drift-progress-fill drift-progress-fill--${fillClass}`;
                fill.style.width = `${Math.min(100, rate * 100)}%`;
                bar.appendChild(fill);

                const dText = document.createElement("span");
                dText.style.fontWeight = "600";
                dText.textContent = `${(rate * 100).toFixed(2)}%`;

                dWrapper.append(bar, dText);
                driftCell.appendChild(dWrapper);

                const impactCell = document.createElement("td");
                const catInfo = rateLabel(rate);
                const badge = document.createElement("span");
                badge.className = `status-badge status-badge--${catInfo.variant}`;
                badge.textContent = catInfo.label;
                impactCell.appendChild(badge);

                tr.append(catCell, countCell, driftCell, impactCell);
                catTbody.appendChild(tr);
            });
        }
    }

    function renderEnsembleTable(tbody, trials) {
        if (!tbody) return;
        tbody.innerHTML = "";

        if (!trials || trials.length === 0) {
            const tr = document.createElement("tr");
            tr.innerHTML = '<td colspan="8" style="text-align:center; padding: 20px; color: var(--color-text-muted);">No matching trials found.</td>';
            tbody.appendChild(tr);
            return;
        }

        trials.forEach((trial) => {
            const row = document.createElement("tr");

            // Trial ID
            const idCell = document.createElement("td");
            const code = document.createElement("code");
            code.style.fontSize = "12px";
            code.textContent = trial.trial_id;
            idCell.appendChild(code);

            // Category badge
            const catCell = document.createElement("td");
            const catBadge = document.createElement("span");
            catBadge.className = "status-badge status-badge--muted";
            catBadge.textContent = (trial.scenario_category || "").replaceAll("_", " ");
            catCell.appendChild(catBadge);

            // Workload
            const workCell = document.createElement("td");
            workCell.style.fontWeight = "600";
            workCell.textContent = trial.target_service;

            // Spec
            const specCell = document.createElement("td");
            specCell.style.fontSize = "13px";
            specCell.style.color = "var(--color-text-muted)";
            specCell.textContent = trial.parameter_spec;

            // Drift Rate with Progress Bar
            const driftCell = document.createElement("td");
            const dWrapper = document.createElement("div");
            dWrapper.className = "drift-cell-wrapper";

            const dPct = trial.d_rate == null ? "n/a " : (trial.d_rate * 100).toFixed(1);
            let fillClass = "green";
            if (trial.d_rate >= 0.5) fillClass = "red";
            else if (trial.d_rate >= 0.2) fillClass = "yellow";

            const bar = document.createElement("div");
            bar.className = "drift-progress-bar";
            const fill = document.createElement("div");
            fill.className = `drift-progress-fill drift-progress-fill--${fillClass}`;
            fill.style.width = `${Math.min(100, trial.d_rate * 100)}%`;
            bar.appendChild(fill);

            const dText = document.createElement("span");
            dText.style.fontWeight = "600";
            dText.textContent = `${dPct}%`;

            dWrapper.append(bar, dText);
            driftCell.appendChild(dWrapper);

            // Exposure Delta
            const expCell = document.createElement("td");
            const expBadge = document.createElement("span");
            if (trial.delta_e_rate == null) {
                expBadge.className = "status-badge status-badge--muted";
                expBadge.textContent = "n/a";
            } else {
                const ePct = (trial.delta_e_rate * 100).toFixed(1);
                expBadge.className = `status-badge status-badge--${trial.delta_e_rate > 0 ? "danger" : "muted"}`;
                expBadge.textContent = `${trial.delta_e_rate > 0 ? "+" : ""}${ePct}%`;
            }
            expCell.appendChild(expBadge);

            // Displaced Pods
            const dispCell = document.createElement("td");
            dispCell.textContent = `${trial.migrated_pods || 0}`;

            // Status
            const statusCell = document.createElement("td");
            const statusBadge = document.createElement("span");
            statusBadge.className = `status-badge status-badge--${trial.status === "COMPLETED" ? "success" : "danger"}`;
            statusBadge.textContent = `${trial.status} · ${trial.execution_mode}`;
            statusCell.appendChild(statusBadge);

            row.append(idCell, catCell, workCell, specCell, driftCell, expCell, dispCell, statusCell);
            tbody.appendChild(row);
        });
    }

    function initEnsembleInteractions(root, ensemble, benchmarks) {
        const filterBtns = root.querySelectorAll("[data-role='ensemble-category-filters'] .filter-btn");
        const searchInput = root.querySelector("[data-role='ensemble-search']");
        const tbody = root.querySelector("[data-role='ensemble-table-body']");

        let activeCat = "ALL";
        let searchQuery = "";

        const allItems = ensemble?.trials ? [...ensemble.trials] : [];

        function filterAndRender() {
            let filtered = allItems;

            if (activeCat !== "ALL") {
                filtered = filtered.filter((item) => item.scenario_category === activeCat);
            }

            if (searchQuery.trim() !== "") {
                const q = searchQuery.toLowerCase().trim();
                filtered = filtered.filter((item) => {
                    return [item.trial_id, item.target_service, item.scenario_category, item.parameter_spec]
                        .some((field) => field?.toLowerCase().includes(q));
                });
            }

            renderEnsembleTable(tbody, filtered);
        }

        filterBtns.forEach((btn) => {
            btn.addEventListener("click", () => {
                filterBtns.forEach((b) => b.classList.remove("filter-btn--active"));
                btn.classList.add("filter-btn--active");
                activeCat = btn.dataset.cat || "ALL";
                filterAndRender();
            });
        });

        if (searchInput) {
            searchInput.addEventListener("input", (e) => {
                searchQuery = e.target.value;
                filterAndRender();
            });
        }

        filterAndRender();
    }

    async function loadEnsemble() {
        const root = document.getElementById("tab-scenarios");
        if (!root) return;

        try {
            const [ensembleResp, benchmarksResp] = await Promise.all([
                fetch("/api/deployments/benchmarks/ensemble").catch(() => null),
                fetch("/api/deployments/chaos/benchmarks").catch(() => null),
            ]);

            const ensemble = ensembleResp?.ok ? await ensembleResp.json() : null;
            const benchmarks = benchmarksResp?.ok ? await benchmarksResp.json() : null;

            if (ensemble) {
                renderEnsembleSummary(root.querySelector('[data-role="ensemble-summary-grid"]'), ensemble);
                renderSensitivityBreakdowns(root, ensemble);
            }
            initEnsembleInteractions(root, ensemble, benchmarks);
        } catch (err) {
            console.warn("Failed to load ensemble benchmark data:", err);
        }
    }

    function init() {
        const root = document.getElementById("tab-scenarios");
        if (!root) return;

        const select = root.querySelector("[data-role='markov-scenario-select']") || document.getElementById("scenario-matrix-select");
        const toggleLatexBtn = root.querySelector("[data-role='markov-toggle-latex-btn']") || document.getElementById("btn-copy-latex-matrix");
        const copyLatexBtn = root.querySelector("[data-role='markov-copy-latex-btn']");
        const latexPreview = root.querySelector("[data-role='markov-latex-preview']");
        const latexCodeEl = root.querySelector("[data-role='markov-latex-code']");

        if (select) {
            select.addEventListener("change", function () {
                loadMarkovMatrix(this.value).catch(warn("Matrix load failed"));
            });
            loadMarkovMatrix(select.value).catch(warn("Matrix load failed"));
        }

        if (toggleLatexBtn && latexPreview) {
            toggleLatexBtn.addEventListener("click", function () {
                const isHidden = latexPreview.style.display === "none";
                latexPreview.style.display = isHidden ? "block" : "none";
                toggleLatexBtn.textContent = isHidden ? "Hide LaTeX Table" : "View LaTeX Table";
                if (latexCodeEl) {
                    latexCodeEl.textContent = currentLatexCode || "% No LaTeX code generated";
                }
            });
        }

        if (copyLatexBtn && latexCodeEl) {
            copyLatexBtn.addEventListener("click", function () {
                navigator.clipboard.writeText(latexCodeEl.textContent).then(() => {
                    const original = copyLatexBtn.textContent;
                    copyLatexBtn.textContent = "Copied to Clipboard!";
                    setTimeout(() => {
                        copyLatexBtn.textContent = original;
                    }, 2000);
                }).catch(warn("Clipboard copy failed"));
            });
        }

        const runAllBtn = document.getElementById("btn-run-all-benchmarks");
        if (runAllBtn) {
            runAllBtn.addEventListener("click", async function () {
                const origText = runAllBtn.textContent;
                runAllBtn.textContent = "⏳ Executing 8 Scenarios...";
                runAllBtn.disabled = true;
                try {
                    const resp = await fetch("/api/deployments/chaos/benchmarks");
                    if (resp.ok) {
                        alert("All 8 dry-run scenarios executed (scripted placements, no cluster changes).");
                        if (select) await loadMarkovMatrix(select.value);
                        await loadEnsemble();
                    }
                } catch (e) {
                    alert("Error running chaos benchmarks: " + e);
                } finally {
                    runAllBtn.textContent = origText;
                    runAllBtn.disabled = false;
                }
            });
        }

        // Fetch ensemble benchmark dataset
        loadEnsemble().catch(warn("Ensemble load failed"));
    }

    return {
        init: init,
        loadMarkovMatrix: loadMarkovMatrix,
        loadEnsemble: loadEnsemble,
    };
})();
