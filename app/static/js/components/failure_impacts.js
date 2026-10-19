/*
 * Failure Impact Prediction page script.
 * Owned by: Member 3 (K.V.D.Y.R. Karawita).
 *
 * Runs Component 3's deterministic simulation over validated runtime inputs: a
 * frozen workflow from Component 1 and the connected cluster's live topology
 * from Component 2. All survival and blast-radius calculations happen in the
 * Python engine. The result is a prediction about a virtual failure, not an
 * observation, and no live cluster is changed. When the inputs are missing or
 * incomplete the page shows why; it never substitutes other data.
 */

(function () {
    const RUNTIME_IMPACT_ENDPOINT = "/api/failure-impacts/runtime";
    const DISCOVERY_ENDPOINT = "/api/deployments/discovery";
    const { fetchJson } = window.ResilienceApi;
    const { describeError, describeDiscovery, renderStateCard, applyBadge } = window.ResilienceConnection;
    const {
        formatPercentage,
        formatCodeLabel,
        initJsonPanel,
        updateLastRefreshed,
    } = window.ResilienceUi;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const nodeSelector = document.querySelector('[data-role="node-selector"]');
    const workflowIdInput = document.querySelector('[data-role="workflow-id"]');
    const namespaceInput = document.querySelector('[data-role="namespace"]');
    const inputModeNote = document.querySelector('[data-role="input-mode-note"]');
    const inputSourceBadge = document.querySelector(
        '[data-role="input-source-badge"]'
    );
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function riskLabel(blastRadius) {
        if (blastRadius >= 80) return { text: "Critical", variant: "danger" };
        if (blastRadius >= 50) return { text: "High", variant: "warning" };
        return { text: "Low", variant: "success" };
    }

    function buildChip(text) {
        const chip = document.createElement("span");
        chip.className = "contract-chip";
        chip.textContent = text;
        return chip;
    }

    function populateNodeSelector(deployment) {
        if (!nodeSelector) return;

        const previousSelection = nodeSelector.value;
        nodeSelector.innerHTML = "";

        deployment.nodes.forEach((node) => {
            const option = document.createElement("option");
            option.value = node.name;
            option.textContent = node.zone
                ? `${node.name} (${node.zone})`
                : node.name;
            nodeSelector.appendChild(option);
        });

        if (
            previousSelection &&
            deployment.nodes.some((node) => node.name === previousSelection)
        ) {
            nodeSelector.value = previousSelection;
        }
    }

    function runtimeSelection() {
        const workflowId = workflowIdInput?.value.trim() || "";
        const namespace = namespaceInput?.value.trim() || "";

        const invalid = (message) => Object.assign(new Error(message), { code: "INVALID_INPUT" });
        if (!workflowId) {
            throw invalid("Enter the frozen workflow ID.");
        }
        if (!namespace) {
            throw invalid("Enter the Kubernetes namespace.");
        }
        return { workflowId, namespace };
    }

    function impactEndpoint(selection, failedNode) {
        const params = new URLSearchParams({
            workflow_id: selection.workflowId,
            failed_node: failedNode,
            namespace: selection.namespace,
        });
        return `${RUNTIME_IMPACT_ENDPOINT}?${params.toString()}`;
    }

    function describeInput(discovery) {
        const described = describeDiscovery(discovery);
        applyBadge(inputSourceBadge, described.badge);
        if (inputModeNote) {
            inputModeNote.textContent =
                `${described.headline} ${described.provenance} ` +
                "The result below is a prediction for a virtual failure, not an observation.";
        }
    }

    function renderChipList(container, values) {
        container.innerHTML = "";
        if (!values.length) {
            const empty = document.createElement("span");
            empty.className = "muted";
            empty.textContent = "None";
            container.appendChild(empty);
            return;
        }
        values.forEach((value) => container.appendChild(buildChip(value)));
    }

    function renderFailureChain(container, impact, deployment) {
        container.innerHTML = "";

        const lostPods = deployment.services
            .flatMap((service) =>
                service.pods
                    .filter((pod) => pod.node === impact.failedNode)
                    .map((pod) => pod.name)
            );

        const steps = [`${impact.failedNode} failed`];
        if (lostPods.length) {
            steps.push(`${lostPods.join(" and ")} lost`);
        }
        impact.failedServices.forEach((service) => {
            steps.push(`${service} unavailable`);
        });
        impact.failedWorkflows.forEach((workflow) => {
            steps.push(`${workflow} workflow failed`);
        });

        steps.forEach((step, index) => {
            const stepElement = document.createElement("div");
            stepElement.className =
                "failure-chain__step" + (index === 0 ? " failure-chain__step--origin" : "");
            stepElement.textContent = step;
            container.appendChild(stepElement);

            if (index < steps.length - 1) {
                const arrow = document.createElement("div");
                arrow.className = "failure-chain__arrow";
                arrow.setAttribute("aria-hidden", "true");
                arrow.textContent = "↓";
                container.appendChild(arrow);
            }
        });
    }

    function renderRootCauses(container, rootCauses) {
        container.innerHTML = "";

        if (!rootCauses.length) {
            const empty = document.createElement("p");
            empty.className = "muted";
            empty.textContent = "No root causes reported.";
            container.appendChild(empty);
            return;
        }

        rootCauses.forEach((cause) => {
            const item = document.createElement("div");
            item.className = "stack-sm";
            item.style.marginBottom = "var(--space-md)";

            const rows = [
                ["Type", formatCodeLabel(cause.type)],
                ["Service", cause.service],
                ["Failure Domain", cause.failureDomain],
                [
                    "Concentration",
                    cause.concentration !== null && cause.concentration !== undefined
                        ? formatPercentage(cause.concentration * 100)
                        : "—",
                ],
            ];

            rows.forEach(([label, value]) => {
                const row = document.createElement("div");
                row.style.display = "flex";
                row.style.justifyContent = "space-between";
                row.style.fontSize = "13px";
                row.style.padding = "4px 0";

                const labelSpan = document.createElement("span");
                labelSpan.className = "muted";
                labelSpan.textContent = label;

                const valueSpan = document.createElement("span");
                valueSpan.style.fontWeight = "600";
                valueSpan.textContent = value;

                row.append(labelSpan, valueSpan);
                item.appendChild(row);
            });

            container.appendChild(item);
        });
    }

    function renderNodeRanking(tbody, ranking) {
        tbody.innerHTML = "";

        ranking.forEach((entry, index) => {
            const risk = riskLabel(entry.blastRadius);
            const row = document.createElement("tr");

            const rankCell = document.createElement("td");
            rankCell.textContent = String(index + 1);

            const nodeCell = document.createElement("td");
            nodeCell.textContent = entry.node;

            const blastCell = document.createElement("td");
            blastCell.textContent = formatPercentage(entry.blastRadius);

            const riskCell = document.createElement("td");
            const badge = document.createElement("span");
            badge.className = `status-badge status-badge--${risk.variant}`;
            badge.textContent = risk.text;
            riskCell.appendChild(badge);

            row.append(rankCell, nodeCell, blastCell, riskCell);
            tbody.appendChild(row);
        });
    }

    function setBusy(busy) {
        [reloadButton, nodeSelector, workflowIdInput, namespaceInput].forEach((control) => {
            if (control) control.disabled = busy;
        });
    }

    // Reads the connected cluster's topology and fills the node selector. Never keeps a previous
    // cluster's nodes: a failed read empties the selector and shows the failure.
    async function loadTopology() {
        const namespace = namespaceInput?.value.trim() || "default";
        if (nodeSelector) nodeSelector.replaceChildren();
        try {
            const discovery = await fetchJson(
                `${DISCOVERY_ENDPOINT}?namespace=${encodeURIComponent(namespace)}`
            );
            populateNodeSelector(discovery.graph);
            describeInput(discovery);
            return discovery.graph;
        } catch (error) {
            applyBadge(inputSourceBadge, { variant: "danger", text: "Not showing live data" });
            renderStateCard(region, describeError(error), { onRetry: loadTopology });
            return null;
        }
    }

    async function loadPage() {
        setBusy(true);
        const previousNode = nodeSelector?.value;

        try {
            const selection = runtimeSelection();
            const deployment = await loadTopology();
            if (!deployment) return;
            if (previousNode && deployment.nodes.some((node) => node.name === previousNode)) {
                nodeSelector.value = previousNode;
            }

            const failedNode = nodeSelector?.value || deployment.nodes[0]?.name;
            if (!failedNode) {
                throw new Error("The discovered topology contains no worker nodes.");
            }
            const impact = await fetchJson(impactEndpoint(selection, failedNode));

            region.innerHTML = "";
            region.appendChild(template.content.cloneNode(true));

            const risk = riskLabel(impact.blastRadius);

            setMetric(region, "impact-failed-node", impact.failedNode);
            setMetric(region, "impact-blast-radius", formatPercentage(impact.blastRadius));
            setMetric(
                region,
                "impact-survivability",
                formatPercentage(impact.survivability)
            );
            setMetric(
                region,
                "impact-failed-workflow-count",
                String(impact.failedWorkflows.length)
            );
            setMetric(
                region,
                "impact-blast-radius-large",
                formatPercentage(impact.blastRadius)
            );
            setMetric(
                region,
                "impact-survivability-large",
                formatPercentage(impact.survivability)
            );

            const blastBar = region.querySelector('[data-role="impact-blast-radius-bar"]');
            if (blastBar) blastBar.style.width = formatPercentage(impact.blastRadius);

            const survivalBar = region.querySelector(
                '[data-role="impact-survivability-bar"]'
            );
            if (survivalBar) survivalBar.style.width = formatPercentage(impact.survivability);

            const blastTag = region.querySelector('[data-role="impact-blast-radius-tag"]');
            if (blastTag) {
                blastTag.textContent = `${risk.text} Impact`;
                blastTag.className = `status-badge status-badge--${risk.variant} impact-stat__tag`;
            }

            renderFailureChain(
                region.querySelector('[data-role="failure-chain"]'),
                impact,
                deployment
            );
            renderChipList(
                region.querySelector('[data-role="impact-failed-services"]'),
                impact.failedServices
            );
            renderChipList(
                region.querySelector('[data-role="impact-failed-workflows"]'),
                impact.failedWorkflows
            );
            renderRootCauses(
                region.querySelector('[data-role="impact-root-causes"]'),
                impact.rootCauses
            );
            renderNodeRanking(
                region.querySelector('[data-role="impact-node-ranking-body"]'),
                impact.nodeRiskRanking
            );

            initJsonPanel(region.querySelector("#impact-raw-json"), impact);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            renderStateCard(region, describeError(error), { onRetry: loadPage });
        } finally {
            setBusy(false);
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    if (namespaceInput) {
        namespaceInput.addEventListener("change", loadTopology);
    }

    // On open: read the topology so a node can be chosen. No prediction runs until a workflow is named.
    document.addEventListener("DOMContentLoaded", loadTopology);
})();
