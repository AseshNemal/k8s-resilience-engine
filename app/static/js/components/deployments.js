/*
 * Deployment Topology Modeling page script.
 * Owned by: Member 2 (P.A.M. Minara).
 *
 * Only fetches /api/deployments/sample and formats/aggregates it for
 * display (grouping by zone, counting pods, finding the max of the
 * API-provided replicaConcentration field). No concentration or exposure
 * research calculation happens here — replicaConcentration is a value the
 * API already returns; the Low/Moderate/High exposure label below is an
 * explicitly-marked UI display threshold, not a research classification.
 */

(function () {
    let currentEndpoint = "/api/deployments/sample";
    const { fetchJson } = window.ResilienceApi;
    const { setError, formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const toggleSourceButton = document.querySelector('[data-role="toggle-source-button"]');
    const sourceBadge = document.querySelector('[data-role="source-badge"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function exposureLabel(concentration) {
        if (concentration >= 0.8) return { text: "High Exposure", variant: "danger" };
        if (concentration >= 0.5) return { text: "Moderate Exposure", variant: "warning" };
        return { text: "Low Exposure", variant: "success" };
    }

    function buildPodBadge(pod) {
        const badge = document.createElement("div");
        badge.className = "pod-badge";

        const name = document.createElement("span");
        name.className = "pod-badge__name";
        name.textContent = `${pod.name}`;

        const status = document.createElement("span");
        status.className = "pod-badge__status";

        const dot = document.createElement("span");
        dot.className = `pod-dot ${pod.ready ? "pod-dot--ready" : "pod-dot--not-ready"}`;
        dot.setAttribute("aria-hidden", "true");

        const statusText = document.createElement("span");
        let text = pod.ready ? "Ready" : "Not ready";
        if (pod.restartCount > 0) {
            text += ` · ${pod.restartCount} restarts`;
        }
        statusText.textContent = text;
        statusText.style.color = pod.restartCount > 0 ? "var(--color-warning)" : "";

        status.append(dot, statusText);
        badge.append(name, status);
        return badge;
    }

    function renderZoneTopology(container, deployment) {
        container.innerHTML = "";

        const allPods = deployment.services.flatMap((service) =>
            service.pods.map((pod) => ({ ...pod, service: service.name }))
        );

        const zones = new Map();
        deployment.nodes.forEach((node) => {
            const zoneKey = node.zone || "Unassigned";
            if (!zones.has(zoneKey)) zones.set(zoneKey, []);
            zones.get(zoneKey).push(node);
        });

        zones.forEach((nodes, zoneName) => {
            const zoneCard = document.createElement("div");
            zoneCard.className = "zone-card";

            const title = document.createElement("div");
            title.className = "zone-card__title";
            title.textContent = zoneName;
            zoneCard.appendChild(title);

            nodes.forEach((node) => {
                const nodeCard = document.createElement("div");
                nodeCard.className = "node-card";

                const header = document.createElement("div");
                header.className = "node-card__header";
                const nodeName = document.createElement("span");
                nodeName.textContent = node.name;
                const nodeZone = document.createElement("span");
                nodeZone.className = "muted";
                nodeZone.style.fontWeight = "400";
                nodeZone.style.fontSize = "12px";
                nodeZone.textContent = node.zone || "";
                header.append(nodeName, nodeZone);

                const specs = document.createElement("div");
                specs.className = "node-card__specs";
                const specParts = [];
                if (node.cpuAvailable !== null && node.cpuAvailable !== undefined) {
                    specParts.push(`${node.cpuAvailable} CPU`);
                }
                if (node.memoryAvailable !== null && node.memoryAvailable !== undefined) {
                    specParts.push(`${node.memoryAvailable} GB memory`);
                }
                specs.textContent = specParts.join(" · ");

                const podsForNode = allPods.filter((pod) => pod.node === node.name);
                const podsContainer = document.createElement("div");
                podsContainer.className = "node-card__pods";

                if (podsForNode.length) {
                    podsForNode.forEach((pod) => {
                        podsContainer.appendChild(buildPodBadge(pod));
                    });
                } else {
                    const empty = document.createElement("div");
                    empty.className = "node-card__empty";
                    empty.textContent = "No replicas placed on this node.";
                    podsContainer.appendChild(empty);
                }

                nodeCard.append(header, specs, podsContainer);
                zoneCard.appendChild(nodeCard);
            });

            container.appendChild(zoneCard);
        });
    }

    function renderConcentrationPanel(container, service) {
        container.innerHTML = "";
        if (!service) return;

        const value = document.createElement("div");
        value.className = "concentration-panel__value";
        value.textContent = formatRatioAsPercentage(service.replicaConcentration);

        const body = document.createElement("div");
        body.className = "concentration-panel__body";

        const label = document.createElement("div");
        label.className = "concentration-panel__label";
        label.textContent = `Replica Concentration — ${service.name}`;

        const bar = document.createElement("div");
        bar.className = "progress-bar";
        const fill = document.createElement("div");
        const exposure = exposureLabel(service.replicaConcentration || 0);
        fill.className = `progress-bar__fill progress-bar__fill--${
            exposure.variant === "danger"
                ? "danger"
                : exposure.variant === "warning"
                ? "warning"
                : "success"
        }`;
        fill.style.width = formatRatioAsPercentage(service.replicaConcentration);
        bar.appendChild(fill);

        const exposureText = document.createElement("div");
        exposureText.className = "muted";
        exposureText.style.marginTop = "6px";
        exposureText.style.fontSize = "12px";
        exposureText.textContent = exposure.text;

        body.append(label, bar, exposureText);
        container.append(value, body);
    }

    function renderServiceTable(tbody, deployment) {
        tbody.innerHTML = "";

        deployment.services.forEach((service) => {
            const nodesUsed = new Set(service.pods.map((pod) => pod.node)).size;
            const exposure = exposureLabel(service.replicaConcentration || 0);

            const row = document.createElement("tr");

            const nameCell = document.createElement("td");
            nameCell.textContent = service.name;

            const declaredCell = document.createElement("td");
            declaredCell.textContent = String(service.declaredReplicas);

            const readyCell = document.createElement("td");
            readyCell.textContent = String(service.readyReplicas);

            const nodesCell = document.createElement("td");
            nodesCell.textContent = String(nodesUsed);

            const concentrationCell = document.createElement("td");
            concentrationCell.textContent = formatRatioAsPercentage(
                service.replicaConcentration
            );

            const exposureCell = document.createElement("td");
            const badge = document.createElement("span");
            badge.className = `status-badge status-badge--${exposure.variant}`;
            badge.textContent = exposure.text.replace(" Exposure", "");
            exposureCell.appendChild(badge);

            const driftCell = document.createElement("td");
            const driftBadge = document.createElement("span");
            if (service.driftDetected) {
                driftBadge.className = "status-badge status-badge--warning";
                driftBadge.textContent = "Drift Detected";
                if (service.driftReasons && service.driftReasons.length) {
                    driftBadge.title = service.driftReasons.join("; ");
                }
            } else {
                driftBadge.className = "status-badge status-badge--success";
                driftBadge.textContent = "Compliant";
            }
            driftCell.appendChild(driftBadge);

            row.append(
                nameCell,
                declaredCell,
                readyCell,
                nodesCell,
                concentrationCell,
                exposureCell,
                driftCell
            );
            tbody.appendChild(row);
        });
    }

    function renderWarning(container, service) {
        container.innerHTML = "";
        if (!service || (service.replicaConcentration || 0) < 0.5) return;

        const nodeCounts = new Map();
        service.pods.forEach((pod) => {
            nodeCounts.set(pod.node, (nodeCounts.get(pod.node) || 0) + 1);
        });

        let busiestNode = null;
        let busiestCount = 0;
        nodeCounts.forEach((count, node) => {
            if (count > busiestCount) {
                busiestCount = count;
                busiestNode = node;
            }
        });

        if (!busiestNode) return;

        const card = document.createElement("div");
        card.className = "card";
        card.style.borderLeft = "4px solid var(--color-warning)";

        const text = document.createElement("p");
        text.textContent = `${busiestCount} of ${service.pods.length} ${service.name} replicas are placed on ${busiestNode}.`;

        const note = document.createElement("p");
        note.className = "ui-note";
        note.style.marginTop = "6px";
        note.textContent = "This is a sample demonstration, not a confirmed research finding.";

        card.append(text, note);
        container.appendChild(card);
    }

    function renderEnsembleSummary(container, ensemble) {
        if (!container || !ensemble) return;
        container.innerHTML = "";

        const meanPct = (ensemble.mean_d_rate * 100).toFixed(1);
        const stdDevPct = (ensemble.std_dev_d_rate * 100).toFixed(1);
        const ciLow = (ensemble.confidence_interval_95[0] * 100).toFixed(1);
        const ciHigh = (ensemble.confidence_interval_95[1] * 100).toFixed(1);
        const deltaEPct = (ensemble.mean_delta_e_rate * 100).toFixed(1);

        const items = [
            {
                label: "Total Trials",
                value: `${ensemble.total_trials}`,
                sub: "Empirical perturbations completed",
            },
            {
                label: "Mean Drift Rate (μ)",
                value: `${meanPct}%`,
                sub: `Standard deviation σ = ${stdDevPct}%`,
            },
            {
                label: "95% Confidence Interval",
                value: `[${ciLow}%, ${ciHigh}%]`,
                sub: "True population drift estimate",
                isCi: true,
                ciLow: parseFloat(ciLow),
                ciHigh: parseFloat(ciHigh),
                mean: parseFloat(meanPct),
            },
            {
                label: "Failure Exposure (ΔE)",
                value: `+${deltaEPct}%`,
                sub: "Net increase in SPOF risk",
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

        if (svcTbody && ensemble.per_service_drift) {
            svcTbody.innerHTML = "";
            const tierMap = {
                "multi_workload": { label: "CRITICAL DRIFT", variant: "danger" },
                "productcatalogservice": { label: "MODERATE DRIFT", variant: "warning" },
                "paymentservice": { label: "MODERATE DRIFT (SPOF)", variant: "danger" },
                "cartservice": { label: "MODERATE DRIFT", variant: "warning" },
                "checkoutservice": { label: "MINIMAL DRIFT", variant: "success" },
                "shippingservice": { label: "MINIMAL DRIFT", variant: "success" },
                "frontend": { label: "MINIMAL DRIFT", variant: "success" },
                "all_services": { label: "CONTROL BASELINE", variant: "muted" },
            };

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
                const tierInfo = tierMap[svc] || { label: "STABLE", variant: "success" };
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
            const catMap = {
                "NODE_DRAIN": { label: "HIGH DISPLACEMENT", variant: "danger" },
                "SCALE_DOWN_LOSS": { label: "BALANCED RE-PLACEMENT", variant: "warning" },
                "POD_DELETION": { label: "BALANCED RE-PLACEMENT", variant: "warning" },
                "ROLLING_UPDATE": { label: "BALANCED RE-PLACEMENT", variant: "warning" },
                "SCALE_UP_STRESS": { label: "STABLE", variant: "success" },
                "BASELINE": { label: "STABLE", variant: "muted" },
            };

            const sortedCategories = Object.entries(ensemble.per_disruption_drift).sort((a, b) => b[1] - a[1]);

            sortedCategories.forEach(([cat, rate]) => {
                const tr = document.createElement("tr");

                const catCell = document.createElement("td");
                catCell.style.fontWeight = "600";
                catCell.textContent = cat.replace(/_/g, " ");

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
                const catInfo = catMap[cat] || { label: "STABLE", variant: "success" };
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
            catBadge.textContent = trial.scenario_category.replace(/_/g, " ");
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

            const dPct = (trial.d_rate * 100).toFixed(1);
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
            const ePct = (trial.delta_e_rate * 100).toFixed(1);
            const expBadge = document.createElement("span");
            if (trial.delta_e_rate > 0) {
                expBadge.className = "status-badge status-badge--danger";
                expBadge.textContent = `+${ePct}%`;
            } else {
                expBadge.className = "status-badge status-badge--muted";
                expBadge.textContent = "0.0%";
            }
            expCell.appendChild(expBadge);

            // Displaced Pods
            const dispCell = document.createElement("td");
            dispCell.textContent = `${trial.migrated_pods || 0}`;

            // Status
            const statusCell = document.createElement("td");
            const statusBadge = document.createElement("span");
            statusBadge.className = "status-badge status-badge--success";
            statusBadge.textContent = "COMPLETED";
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

        // Combine ensemble trials with node_cordon and resource_contention benchmark records if present
        let allItems = (ensemble && ensemble.trials) ? [...ensemble.trials] : [];

        // Add Node Cordon and Resource Contention from benchmarks if available
        if (benchmarks && benchmarks.scenario_results) {
            benchmarks.scenario_results.forEach((s) => {
                const cat = s.disruption_type;
                if (cat === "NODE_CORDON" || cat === "RESOURCE_CONTENTION") {
                    allItems.push({
                        trial_id: s.scenario_id,
                        scenario_category: cat,
                        target_service: s.target_service || (s.drifted_services && s.drifted_services.length ? s.drifted_services.join(", ") : "multi_workload"),
                        parameter_spec: cat === "NODE_CORDON"
                            ? "Unschedulable node diversion (worker-2 cordoned)"
                            : "CPU & memory pressure under quota limits (worker-3)",
                        d_rate: s.d_rate || 0,
                        delta_e_rate: s.delta_e_rate || 0,
                        migrated_pods: 0,
                        status: s.status || "COMPLETED",
                    });
                }
            });
        }

        function filterAndRender() {
            let filtered = allItems;
            if (activeCat !== "ALL") {
                filtered = filtered.filter((t) => {
                    if (activeCat === "BASELINE") {
                        return t.scenario_category === "BASELINE" || t.scenario_category === "INITIAL_DEPLOYMENT";
                    }
                    if (activeCat === "ROLLING_UPDATE") {
                        return t.scenario_category === "ROLLING_UPDATE" || t.scenario_category === "ROLLING_UPDATE_CHURN";
                    }
                    if (activeCat === "POD_DELETION") {
                        return t.scenario_category === "POD_DELETION" || t.scenario_category === "POD_DELETION_CHAOS";
                    }
                    if (activeCat === "NODE_DRAIN") {
                        return t.scenario_category === "NODE_DRAIN" || t.scenario_category === "NODE_DRAIN_EVICTION";
                    }
                    return t.scenario_category === activeCat;
                });
            }
            if (searchQuery.trim()) {
                const q = searchQuery.toLowerCase();
                filtered = filtered.filter((t) =>
                    t.trial_id.toLowerCase().includes(q) ||
                    t.target_service.toLowerCase().includes(q) ||
                    t.parameter_spec.toLowerCase().includes(q) ||
                    t.scenario_category.toLowerCase().includes(q)
                );
            }
            renderEnsembleTable(tbody, filtered);
        }

        filterBtns.forEach((btn) => {
            btn.addEventListener("click", () => {
                filterBtns.forEach((b) => b.classList.remove("filter-btn--active"));
                btn.classList.add("filter-btn--active");
                activeCat = btn.getAttribute("data-cat");
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

    async function loadMarkovMatrix(root, scenarioId) {
        if (!root) return;
        const content = root.querySelector("[data-role='markov-matrix-content']");
        const latexCodeEl = root.querySelector("[data-role='markov-latex-code']");
        if (!content) return;

        content.innerHTML = '<div class="loading-state">Loading Markov Transition Matrix…</div>';

        try {
            const data = await fetchJson(`/api/deployments/chaos/matrix/${scenarioId}`);
            content.innerHTML = "";

            if (!data || !data.nodes || !data.transition_matrix) {
                content.innerHTML = '<p class="text-muted" style="padding:15px;">No transition data available for this scenario.</p>';
                return;
            }

            const wrapper = document.createElement("div");
            wrapper.className = "markov-grid-wrapper";

            const tableScroll = document.createElement("div");
            tableScroll.className = "table-scroll";

            const table = document.createElement("table");
            table.className = "markov-grid-table";

            // Header Row
            const thead = document.createElement("thead");
            const headerRow = document.createElement("tr");

            const cornerTh = document.createElement("th");
            cornerTh.className = "markov-header-corner";
            cornerTh.textContent = "From (T0) \\ To (T1)";
            headerRow.appendChild(cornerTh);

            data.nodes.forEach((nodeName) => {
                const th = document.createElement("th");
                th.textContent = nodeName;
                headerRow.appendChild(th);
            });

            const outTh = document.createElement("th");
            outTh.textContent = "Migrations Out";
            headerRow.appendChild(outTh);

            thead.appendChild(headerRow);
            table.appendChild(thead);

            // Table Body
            const tbody = document.createElement("tbody");
            data.nodes.forEach((srcNode) => {
                const tr = document.createElement("tr");

                const rowNodeTd = document.createElement("td");
                rowNodeTd.className = "markov-row-node";
                rowNodeTd.textContent = srcNode;
                tr.appendChild(rowNodeTd);

                let totalOut = 0;

                data.nodes.forEach((dstNode) => {
                    const td = document.createElement("td");
                    const count = data.transition_matrix[srcNode]?.[dstNode] || 0;

                    if (srcNode === dstNode) {
                        td.className = "markov-cell--stayed";
                        td.innerHTML = `<span>${count}</span> <small style="display:block;font-size:10px;opacity:0.8;">remained</small>`;
                    } else if (count > 0) {
                        td.className = "markov-cell--migration";
                        td.innerHTML = `<span>→ ${count}</span> <small style="display:block;font-size:10px;">displaced</small>`;
                        totalOut += count;
                    } else {
                        td.className = "markov-cell--empty";
                        td.textContent = "—";
                    }

                    tr.appendChild(td);
                });

                const outTd = document.createElement("td");
                outTd.style.fontWeight = "700";
                outTd.textContent = totalOut > 0 ? `→ ${totalOut}` : "0";
                if (totalOut > 0) outTd.style.color = "var(--color-danger)";
                tr.appendChild(outTd);

                tbody.appendChild(tr);
            });

            table.appendChild(tbody);
            tableScroll.appendChild(table);
            wrapper.appendChild(tableScroll);

            // Metrics Row
            const metricsRow = document.createElement("div");
            metricsRow.className = "markov-metrics-row";

            const dRatePct = (data.d_rate * 100).toFixed(1);
            const deltaEPct = (data.delta_e_rate * 100).toFixed(1);

            metricsRow.innerHTML = `
                <div class="markov-metric-pill">
                    <span class="markov-metric-pill__label">Placement Drift Rate (D_rate):</span>
                    <span class="status-badge ${data.d_rate > 0.3 ? 'status-badge--danger' : data.d_rate > 0 ? 'status-badge--warning' : 'status-badge--success'}">${dRatePct}%</span>
                </div>
                <div class="markov-metric-pill">
                    <span class="markov-metric-pill__label">Exposure Shift (ΔE):</span>
                    <span class="status-badge ${data.delta_e_rate > 0 ? 'status-badge--danger' : 'status-badge--muted'}">${data.delta_e_rate > 0 ? '+' : ''}${deltaEPct}%</span>
                </div>
                <div class="markov-metric-pill">
                    <span class="markov-metric-pill__label">Evaluated Nodes:</span>
                    <span class="markov-metric-pill__val">${data.nodes.length}</span>
                </div>
            `;
            wrapper.appendChild(metricsRow);

            // Migration Records
            if (data.migration_records && data.migration_records.length > 0) {
                const migList = document.createElement("div");
                migList.className = "markov-migrations-list";

                data.migration_records.forEach((rec) => {
                    const item = document.createElement("div");
                    item.className = "markov-migration-item";
                    item.innerHTML = `
                        <strong>${rec.from_node} → ${rec.to_node}:</strong>
                        <span>${rec.pod_count} pod(s) displaced</span>
                        <code style="font-size:11px; margin-left: auto;">${(rec.services_affected || []).join(", ")}</code>
                    `;
                    migList.appendChild(item);
                });
                wrapper.appendChild(migList);
            }

            content.appendChild(wrapper);

            if (latexCodeEl) {
                latexCodeEl.textContent = data.latex_table || "% No LaTeX code generated";
            }
        } catch (err) {
            content.innerHTML = `<p class="text-danger" style="padding:10px;">Failed to load transition matrix: ${err.message}</p>`;
        }
    }

    function initMarkovControls(root) {
        if (!root) return;
        const select = root.querySelector("[data-role='markov-scenario-select']");
        const toggleLatexBtn = root.querySelector("[data-role='markov-toggle-latex-btn']");
        const copyLatexBtn = root.querySelector("[data-role='markov-copy-latex-btn']");
        const latexPreview = root.querySelector("[data-role='markov-latex-preview']");
        const latexCodeEl = root.querySelector("[data-role='markov-latex-code']");

        if (select) {
            select.addEventListener("change", () => {
                loadMarkovMatrix(root, select.value);
            });
        }

        if (toggleLatexBtn && latexPreview) {
            toggleLatexBtn.addEventListener("click", () => {
                const isHidden = latexPreview.style.display === "none";
                latexPreview.style.display = isHidden ? "block" : "none";
                toggleLatexBtn.textContent = isHidden ? "Hide LaTeX Table" : "View LaTeX Table";
            });
        }

        if (copyLatexBtn && latexCodeEl) {
            copyLatexBtn.addEventListener("click", () => {
                navigator.clipboard.writeText(latexCodeEl.textContent).then(() => {
                    const original = copyLatexBtn.textContent;
                    copyLatexBtn.textContent = "Copied to Clipboard!";
                    setTimeout(() => {
                        copyLatexBtn.textContent = original;
                    }, 2000);
                });
            });
        }

        const initialScenario = select ? select.value : "07_node_drain_eviction";
        loadMarkovMatrix(root, initialScenario);
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;
        if (toggleSourceButton) toggleSourceButton.disabled = true;

        try {
            const [deployment, ensemble, benchmarks] = await Promise.all([
                fetchJson(currentEndpoint),
                fetchJson("/api/deployments/benchmarks/ensemble").catch((err) => {
                    console.warn("Ensemble endpoint optional fetch error:", err);
                    return null;
                }),
                fetchJson("/api/deployments/chaos/benchmarks").catch((err) => {
                    console.warn("Chaos benchmarks optional fetch error:", err);
                    return null;
                }),
            ]);

            region.innerHTML = "";
            region.appendChild(template.content.cloneNode(true));

            const totalReadyPods = deployment.services.reduce(
                (sum, service) =>
                    sum + service.pods.filter((pod) => pod.ready).length,
                0
            );

            const highestConcentrationService = deployment.services.reduce(
                (highest, service) =>
                    !highest ||
                    (service.replicaConcentration || 0) >
                        (highest.replicaConcentration || 0)
                    ? service
                    : highest,
                null
            );

            setMetric(region, "deployment-node-count", String(deployment.nodes.length));
            setMetric(
                region,
                "deployment-service-count",
                String(deployment.services.length)
            );
            setMetric(region, "deployment-ready-pods", String(totalReadyPods));
            setMetric(
                region,
                "deployment-highest-concentration",
                highestConcentrationService
                    ? formatRatioAsPercentage(
                          highestConcentrationService.replicaConcentration
                      )
                    : "—"
            );

            renderZoneTopology(
                region.querySelector('[data-role="deployment-zone-grid"]'),
                deployment
            );
            renderConcentrationPanel(
                region.querySelector('[data-role="deployment-concentration-panel"]'),
                highestConcentrationService
            );
            renderServiceTable(
                region.querySelector('[data-role="deployment-service-table-body"]'),
                deployment
            );
            renderWarning(
                region.querySelector('[data-role="deployment-warning-region"]'),
                highestConcentrationService
            );

            if (ensemble || benchmarks) {
                if (ensemble) {
                    renderEnsembleSummary(
                        region.querySelector('[data-role="ensemble-summary-grid"]'),
                        ensemble
                    );
                    renderSensitivityBreakdowns(region, ensemble);
                }
                initEnsembleInteractions(region, ensemble, benchmarks);
                initMarkovControls(region);
            }

            initJsonPanel(region.querySelector("#deployment-raw-json"), deployment);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            setError(region, {
                endpoint: currentEndpoint,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load deployment data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
            if (toggleSourceButton) toggleSourceButton.disabled = false;
        }
    }

    if (toggleSourceButton) {
        toggleSourceButton.addEventListener("click", () => {
            if (currentEndpoint === "/api/deployments/sample") {
                currentEndpoint = "/api/deployments/live";
                if (sourceBadge) {
                    sourceBadge.textContent = "Live Cluster (KinD)";
                    sourceBadge.className = "status-badge status-badge--success status-badge--no-dot";
                }
                toggleSourceButton.textContent = "Switch to Sample Data";
            } else {
                currentEndpoint = "/api/deployments/sample";
                if (sourceBadge) {
                    sourceBadge.textContent = "Sample Data";
                    sourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
                }
                toggleSourceButton.textContent = "Switch to Live Cluster (KinD)";
            }
            loadPage();
        });
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
