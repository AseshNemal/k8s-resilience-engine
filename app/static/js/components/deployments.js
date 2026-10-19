/*
 * Deployment Topology Modeling page script.
 * Owned by: Member 2 (P.A.M. Minara).
 *
 * Fetches /api/deployments/discovery (the connected cluster's topology plus how complete it is) and
 * formats/aggregates it for display (grouping by zone, counting pods, finding the max of the
 * API-provided replicaConcentration field). No concentration or exposure research calculation happens
 * here — replicaConcentration is a value the API already returns; the Low/Moderate/High exposure label
 * below is an explicitly-marked UI display threshold, not a research classification.
 *
 * When the cluster cannot be read the page shows that failure and no topology. Unknown values (zone,
 * capacity, restart counts, drift without declared intent) are shown as unknown, never as defaults.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } = window.ResilienceUi;
    const { describeError, describeDiscovery, renderStateCard, applyBadge } = window.ResilienceConnection;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const namespaceInput = document.querySelector('[data-role="namespace"]');
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
        if (pod.restartCount === null || pod.restartCount === undefined) {
            text += " · restarts unknown";
        } else if (pod.restartCount > 0) {
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
            const zoneKey = node.zone || "Zone not reported";
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

    function renderServiceTable(tbody, deployment, declaredState) {
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
            if (declaredState !== "available" && !service.driftDetected) {
                driftBadge.className = "status-badge status-badge--muted";
                driftBadge.textContent = "Not evaluated";
                driftBadge.title = "No declared deployment intent was selected, so compliance cannot be judged.";
            } else if (service.driftDetected) {
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
        note.textContent = "Observed placement, not a prediction: replicas on one node share that node's fate.";

        card.append(text, note);
        container.appendChild(card);
    }

    function renderDiscoveryNotes(container, described) {
        if (!container) return;
        const headline = document.createElement("p");
        headline.textContent = described.headline;
        const provenance = document.createElement("p");
        provenance.className = "ui-note";
        provenance.textContent = described.provenance;
        container.replaceChildren(headline);
        if (described.notes.length) {
            const list = document.createElement("ul");
            described.notes.forEach((note) => {
                const item = document.createElement("li");
                item.textContent = note;
                list.appendChild(item);
            });
            container.appendChild(list);
        }
        container.appendChild(provenance);
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;
        // A refresh never leaves a previous cluster's topology visible.
        region.replaceChildren();
        applyBadge(sourceBadge, { variant: "muted", text: "Loading…" });

        const namespace = (namespaceInput && namespaceInput.value.trim()) || "default";
        try {
            const result = await fetchJson(
                `/api/deployments/discovery?namespace=${encodeURIComponent(namespace)}`
            );
            const deployment = result.graph;
            const described = describeDiscovery(result);
            applyBadge(sourceBadge, described.badge);

            region.appendChild(template.content.cloneNode(true));
            renderDiscoveryNotes(
                region.querySelector('[data-role="deployment-discovery-notes"]'),
                described
            );

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
                deployment,
                result.declaredState
            );
            renderWarning(
                region.querySelector('[data-role="deployment-warning-region"]'),
                highestConcentrationService
            );

            initJsonPanel(region.querySelector("#deployment-raw-json"), result);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            applyBadge(sourceBadge, { variant: "danger", text: "Not showing live data" });
            renderStateCard(region, describeError(error), { onRetry: loadPage });
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
