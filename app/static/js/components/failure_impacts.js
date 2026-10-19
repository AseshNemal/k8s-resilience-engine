/*
 * Failure Impact Prediction page script.
 * Owned by: Member 3 (K.V.D.Y.R. Karawita).
 *
 * Runs Component 3's deterministic simulation over either disclosed contract
 * fixtures or validated runtime inputs from Components 1 and 2. All survival
 * and blast-radius calculations happen in the Python engine. No live cluster
 * is changed.
 */

(function () {
    const DEMO_IMPACT_ENDPOINT = "/api/failure-impacts/simulate";
    const DEMO_DEPLOYMENT_ENDPOINT = "/api/failure-impacts/demo-deployment";
    const PORTFOLIO_DEMO_ENDPOINT = "/api/failure-impacts/portfolio/simulate";
    const RUNTIME_IMPACT_ENDPOINT = "/api/failure-impacts/runtime";
    const PORTFOLIO_RUNTIME_ENDPOINT = "/api/failure-impacts/portfolio/runtime";
    const RUNTIME_DEPLOYMENT_ENDPOINT = "/api/deployments/live";
    const { fetchJson } = window.ResilienceApi;
    const {
        setError,
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
    const runtimeFields = document.querySelector('[data-role="runtime-fields"]');
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
    let cachedDeployment = null;
    let cachedDeploymentKey = null;

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

    function selectedMode() {
        const mode = inputMode?.value;
        if (
            mode === "runtime" ||
            mode === "portfolio-demo" ||
            mode === "portfolio-runtime"
        ) {
            return mode;
        }
        return "demo";
    }

    function runtimeNamespace() {
        const namespace = namespaceInput?.value.trim() || "";
        if (!namespace) {
            throw new Error("Enter the Kubernetes namespace.");
        }
        return namespace;
    }

    function runtimeSelection() {
        const workflowId = workflowIdInput?.value.trim() || "";
        if (!workflowId) {
            throw new Error("Enter the frozen Component 1 workflow ID.");
        }
        const namespace = runtimeNamespace();
        return { workflowId, namespace };
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
            throw new Error(
                "Enter one or more frozen workflow IDs separated by commas."
            );
        }
        if (new Set(workflowIds).size !== workflowIds.length) {
            throw new Error("Workflow IDs must not be duplicated.");
        }
        const namespace = runtimeNamespace();
        return { workflowIds, namespace };
    }

    function requestContext() {
        if (selectedMode() === "demo") {
            return {
                deploymentEndpoint: DEMO_DEPLOYMENT_ENDPOINT,
                deploymentKey: "demo",
                impactEndpoint(failedNode) {
                    return `${DEMO_IMPACT_ENDPOINT}?failed_node=${encodeURIComponent(
                        failedNode
                    )}`;
                },
            };
        }

        if (selectedMode() === "portfolio-demo") {
            return {
                deploymentEndpoint: DEMO_DEPLOYMENT_ENDPOINT,
                deploymentKey: "portfolio-demo",
                impactEndpoint(failedNode) {
                    return `${PORTFOLIO_DEMO_ENDPOINT}?failed_node=${encodeURIComponent(
                        failedNode
                    )}`;
                },
            };
        }

        if (selectedMode() === "runtime") {
            const { workflowId, namespace } = runtimeSelection();
            const namespaceQuery = encodeURIComponent(namespace);
            return {
                deploymentEndpoint: `${RUNTIME_DEPLOYMENT_ENDPOINT}?namespace=${namespaceQuery}`,
                deploymentKey: `runtime:${namespace}`,
                impactEndpoint(failedNode) {
                    const params = new URLSearchParams({
                        workflow_id: workflowId,
                        failed_node: failedNode,
                        namespace,
                    });
                    return `${RUNTIME_IMPACT_ENDPOINT}?${params.toString()}`;
                },
            };
        }

        const { workflowIds, namespace } = portfolioRuntimeSelection();
        const namespaceQuery = encodeURIComponent(namespace);
        return {
            deploymentEndpoint: `${RUNTIME_DEPLOYMENT_ENDPOINT}?namespace=${namespaceQuery}`,
            deploymentKey: `portfolio-runtime:${namespace}`,
            impactEndpoint(failedNode) {
                const params = new URLSearchParams({
                    failed_node: failedNode,
                    namespace,
                });
                workflowIds.forEach((workflowId) => {
                    params.append("workflow_id", workflowId);
                });
                return `${PORTFOLIO_RUNTIME_ENDPOINT}?${params.toString()}`;
            },
        };
    }

    function updateInputModeUi() {
        const mode = selectedMode();
        const runtime = mode === "runtime" || mode === "portfolio-runtime";
        if (runtimeFields) runtimeFields.hidden = !runtime;
        if (workflowIdLabel) {
            workflowIdLabel.textContent = mode === "portfolio-runtime"
                ? "Workflow IDs"
                : "Workflow ID";
        }
        if (inputSourceBadge) {
            inputSourceBadge.textContent = {
                demo: "Contract Fixtures",
                "portfolio-demo": "Portfolio Fixtures",
                runtime: "Validated Runtime",
                "portfolio-runtime": "Runtime Portfolio",
            }[mode];
        }
        if (inputModeNote) {
            inputModeNote.textContent = {
                demo: "Contract-fixture mode — this runs a virtual prediction and never changes a cluster.",
                "portfolio-demo": "Portfolio-fixture mode — evaluates the selected node across two disclosed workflows without changing a cluster.",
                runtime: "Runtime mode — requires configured Component 1 persistence and a frozen workflow, then uses Component 2 topology; simulation remains virtual.",
                "portfolio-runtime": "Runtime portfolio mode — enter comma-separated frozen workflow IDs and evaluate them against one Component 2 topology; simulation remains virtual.",
            }[mode];
        }
    }

    function resetDeploymentCache() {
        cachedDeployment = null;
        cachedDeploymentKey = null;
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

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;
        if (nodeSelector) nodeSelector.disabled = true;
        if (inputMode) inputMode.disabled = true;
        if (workflowIdInput) workflowIdInput.disabled = true;
        if (namespaceInput) namespaceInput.disabled = true;

        let activeEndpoint = DEMO_IMPACT_ENDPOINT;

        try {
            const context = requestContext();
            activeEndpoint = context.impactEndpoint(nodeSelector?.value || "");
            if (
                !cachedDeployment ||
                cachedDeploymentKey !== context.deploymentKey
            ) {
                cachedDeployment = await fetchJson(context.deploymentEndpoint);
                cachedDeploymentKey = context.deploymentKey;
            }
            const deployment = cachedDeployment;
            populateNodeSelector(deployment);

            const failedNode = nodeSelector?.value || deployment.nodes[0]?.name;
            if (!failedNode) {
                throw new Error("The selected topology contains no worker nodes.");
            }
            activeEndpoint = context.impactEndpoint(failedNode);
            const impact = await fetchJson(activeEndpoint);

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
            setError(region, {
                endpoint: activeEndpoint,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load failure-impact data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
            if (nodeSelector) nodeSelector.disabled = false;
            if (inputMode) inputMode.disabled = false;
            if (workflowIdInput) workflowIdInput.disabled = false;
            if (namespaceInput) namespaceInput.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    if (inputMode) {
        inputMode.addEventListener("change", () => {
            updateInputModeUi();
            resetDeploymentCache();
            loadPage();
        });
    }

    if (namespaceInput) {
        namespaceInput.addEventListener("input", resetDeploymentCache);
    }

    document.addEventListener("DOMContentLoaded", () => {
        updateInputModeUi();
        loadPage();
    });
})();
