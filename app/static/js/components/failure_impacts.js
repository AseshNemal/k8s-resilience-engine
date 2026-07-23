/*
 * Failure Impact Prediction page script.
 * Owned by: Member 3 (K.V.D.Y.R. Karawita).
 *
 * Fetches /api/failure-impacts/sample (and /api/deployments/sample only to
 * compose the failure-chain visualization by matching pod.node against
 * failedNode) and formats the results for display. The "Run Sample
 * Simulation" button reloads the same sample endpoint — it does not run a
 * live simulation. No survival/blast-radius calculation happens in this
 * file; risk labels (Low/High/Critical) are explicitly-marked UI
 * classifications over the API's own blastRadius values.
 */

(function () {
    const IMPACT_ENDPOINT = "/api/failure-impacts/sample";
    const DEPLOYMENT_ENDPOINT = "/api/deployments/sample";
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

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;

        try {
            const [impact, deployment] = await Promise.all([
                fetchJson(IMPACT_ENDPOINT),
                fetchJson(DEPLOYMENT_ENDPOINT),
            ]);

            region.innerHTML = "";
            region.appendChild(template.content.cloneNode(true));

            const risk = riskLabel(impact.blastRadius);

            setMetric(region, "scenario-node", impact.failedNode);
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
            setError(region, {
                endpoint: IMPACT_ENDPOINT,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load failure-impact sample data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
