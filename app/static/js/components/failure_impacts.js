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
    const PORTFOLIO_RUNTIME_ENDPOINT = "/api/failure-impacts/portfolio/runtime";
    // The topology is read through the discovery envelope, not the bare graph, so the page can say how
    // complete it is and show an explicit failure when the cluster cannot be read.
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
    const inputMode = document.querySelector('[data-role="input-mode"]');
    const workflowIdInput = document.querySelector('[data-role="workflow-id"]');
    const workflowIdLabel = document.querySelector(
        '[data-role="workflow-id-label"]'
    );
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

    // A missing or invalid input is the person's to fix, so it carries a code the page can tell apart from
    // a cluster or backend failure.
    function invalid(message) {
        return Object.assign(new Error(message), { code: "INVALID_INPUT" });
    }

    // "runtime" is one frozen workflow; "portfolio-runtime" is several, evaluated against one topology.
    function selectedMode() {
        return inputMode?.value === "portfolio-runtime" ? "portfolio-runtime" : "runtime";
    }

    function runtimeNamespace() {
        const namespace = namespaceInput?.value.trim() || "";
        if (!namespace) {
            throw invalid("Enter the Kubernetes namespace.");
        }
        return namespace;
    }

    function runtimeSelection() {
        const workflowId = workflowIdInput?.value.trim() || "";
        if (!workflowId) {
            throw invalid("Enter the frozen workflow ID.");
        }
        return { workflowId, namespace: runtimeNamespace() };
    }

    function portfolioRuntimeSelection() {
        const rawWorkflowIds = workflowIdInput?.value || "";
        const workflowIds = rawWorkflowIds
            .split(",")
            .map((workflowId) => workflowId.trim());

        if (
            !rawWorkflowIds.trim() ||
            workflowIds.some((workflowId) => !workflowId)
        ) {
            throw invalid(
                "Enter one or more frozen workflow IDs separated by commas."
            );
        }
        if (new Set(workflowIds).size !== workflowIds.length) {
            throw invalid("Workflow IDs must not be duplicated.");
        }
        return { workflowIds, namespace: runtimeNamespace() };
    }

    // Where the topology (and so the list of worker nodes) comes from. Only the namespace is needed, so the
    // node list can be shown before a workflow has been named.
    function topologySource() {
        const namespace = runtimeNamespace();
        return { endpoint: `${DISCOVERY_ENDPOINT}?namespace=${encodeURIComponent(namespace)}` };
    }

    // Builds the prediction URL for the selected mode, once the inputs it needs are valid.
    function impactRequest() {
        const mode = selectedMode();

        if (mode === "runtime") {
            const { workflowId, namespace } = runtimeSelection();
            return (failedNode) => {
                const params = new URLSearchParams({
                    workflow_id: workflowId,
                    failed_node: failedNode,
                    namespace,
                });
                return `${RUNTIME_IMPACT_ENDPOINT}?${params.toString()}`;
            };
        }

        const { workflowIds, namespace } = portfolioRuntimeSelection();
        return (failedNode) => {
            const params = new URLSearchParams({
                failed_node: failedNode,
                namespace,
            });
            workflowIds.forEach((workflowId) => {
                params.append("workflow_id", workflowId);
            });
            return `${PORTFOLIO_RUNTIME_ENDPOINT}?${params.toString()}`;
        };
    }

    // Badge and note for a runtime mode, taken from how complete the discovered topology is.
    function describeInput(discovery) {
        const described = describeDiscovery(discovery);
        applyBadge(inputSourceBadge, described.badge);
        if (inputModeNote) {
            inputModeNote.textContent =
                `${described.headline} ${described.provenance} ` +
                "The result below is a prediction for a virtual failure, not an observation.";
        }
    }

    function updateInputModeUi() {
        const mode = selectedMode();
        if (workflowIdLabel) {
            workflowIdLabel.textContent = mode === "portfolio-runtime"
                ? "Workflow IDs"
                : "Workflow ID";
        }
        // Shown until the topology has been read; describeInput() then replaces both with the real status.
        if (inputSourceBadge) {
            inputSourceBadge.textContent = {
                runtime: "Validated Runtime",
                "portfolio-runtime": "Runtime Portfolio",
            }[mode];
        }
        if (inputModeNote) {
            inputModeNote.textContent = {
                runtime: "Runtime mode — requires configured Component 1 persistence and a frozen workflow, then uses Component 2 topology; simulation remains virtual.",
                "portfolio-runtime": "Runtime portfolio mode — enter comma-separated frozen workflow IDs and evaluate them against one Component 2 topology; simulation remains virtual.",
            }[mode];
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

    function renderRootCauses(root, rootCauses) {
        const section = root.querySelector('[data-role="root-causes-section"]');
        const container = root.querySelector('[data-role="impact-root-causes"]');
        if (!section || !container) return;

        const hasRootCauseContract = Array.isArray(rootCauses);
        section.hidden = !hasRootCauseContract;
        container.innerHTML = "";
        if (!hasRootCauseContract) return;

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

    function renderPortfolioOutcomes(root, workflowImpacts) {
        const section = root.querySelector(
            '[data-role="portfolio-workflow-section"]'
        );
        const container = root.querySelector(
            '[data-role="portfolio-workflow-outcomes"]'
        );
        if (!section || !container) return;

        const hasPortfolio = Array.isArray(workflowImpacts);
        section.hidden = !hasPortfolio;
        container.innerHTML = "";
        if (!hasPortfolio) return;

        workflowImpacts.forEach((impact) => {
            const item = document.createElement("div");
            item.className = "workflow-outcome";

            const identity = document.createElement("div");
            identity.className = "workflow-outcome__identity";

            const name = document.createElement("strong");
            name.textContent = impact.workflowId;

            const badge = document.createElement("span");
            badge.className = impact.survives
                ? "status-badge status-badge--success"
                : "status-badge status-badge--danger";
            badge.textContent = impact.survives ? "Survived" : "Failed";
            identity.append(name, badge);

            const explanation = document.createElement("span");
            explanation.className = "workflow-outcome__explanation muted";
            if (impact.failedMandatoryServices.length) {
                explanation.textContent = `Mandatory failures: ${impact.failedMandatoryServices.join(", ")}`;
            } else if (impact.failedServices.length) {
                explanation.textContent = `Optional unavailable services: ${impact.failedServices.join(", ")}`;
            } else {
                explanation.textContent = "All required services survived.";
            }

            item.append(identity, explanation);
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
        [reloadButton, nodeSelector, inputMode, workflowIdInput, namespaceInput].forEach((control) => {
            if (control) control.disabled = busy;
        });
    }

    // Reads the topology for the selected mode and fills the node selector. Never keeps a previous
    // cluster's nodes: a failed read empties the selector and shows the failure.
    async function loadTopology() {
        if (nodeSelector) nodeSelector.replaceChildren();
        try {
            const source = topologySource();
            const discovery = await fetchJson(source.endpoint);
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
            const impactEndpoint = impactRequest();
            const deployment = await loadTopology();
            if (!deployment) return;
            if (previousNode && deployment.nodes.some((node) => node.name === previousNode)) {
                nodeSelector.value = previousNode;
            }

            const failedNode = nodeSelector?.value || deployment.nodes[0]?.name;
            if (!failedNode) {
                throw new Error("The discovered topology contains no worker nodes.");
            }
            const impact = await fetchJson(impactEndpoint(failedNode));

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
                region,
                impact.rootCauses
            );
            renderPortfolioOutcomes(region, impact.workflowImpacts);
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

    if (inputMode) {
        inputMode.addEventListener("change", () => {
            updateInputModeUi();
            loadTopology();
        });
    }

    // On open: read the topology so a node can be chosen. No prediction runs until Run is pressed with
    // valid inputs (a workflow ID for the runtime modes).
    document.addEventListener("DOMContentLoaded", () => {
        updateInputModeUi();
        loadTopology();
    });
})();
