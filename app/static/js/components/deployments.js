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
    const ENDPOINT = "/api/deployments/sample";
    const { fetchJson } = window.ResilienceApi;
    const { setError, formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
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
            container.appendChild(box);
        });
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

    function initEnsembleInteractions(root, ensemble) {
        const filterBtns = root.querySelectorAll("[data-role='ensemble-category-filters'] .filter-btn");
        const searchInput = root.querySelector("[data-role='ensemble-search']");
        const tbody = root.querySelector("[data-role='ensemble-table-body']");

        let activeCat = "ALL";
        let searchQuery = "";

        function filterAndRender() {
            let filtered = ensemble.trials || [];
            if (activeCat !== "ALL") {
                filtered = filtered.filter((t) => t.scenario_category === activeCat);
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

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;

        try {
            const [deployment, ensemble] = await Promise.all([
                fetchJson(ENDPOINT),
                fetchJson("/api/deployments/benchmarks/ensemble").catch((err) => {
                    console.warn("Ensemble endpoint optional fetch error:", err);
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

            if (ensemble) {
                renderEnsembleSummary(
                    region.querySelector('[data-role="ensemble-summary-grid"]'),
                    ensemble
                );
                initEnsembleInteractions(region, ensemble);
            }

            initJsonPanel(region.querySelector("#deployment-raw-json"), deployment);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            setError(region, {
                endpoint: ENDPOINT,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load deployment sample data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
