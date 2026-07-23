/*
 * Recommendation Evaluation and Validation page script.
 * Owned by: Member 4 (L.A. Nemal).
 *
 * Fetches recommendation, validation, deployment, and failure-impact
 * samples and formats/combines them for display. Simple aggregations
 * (min/filter/count over already-returned fields) are presentation only —
 * no blast radius, risk-reduction, confidence, or score is calculated here;
 * those values come straight from the RecommendationReport/ValidationReport
 * API responses. The "Sample Virtual Placement" panel illustrates the
 * round-robin idea the real simulator already implements server-side
 * (app/engine/recommendation_evaluation_validation/simulator.py); no
 * DeploymentGraph is exposed with simulated placement yet, so this view is
 * explicitly labelled as a sample demonstration, not live simulator output.
 */

(function () {
    const ENDPOINTS = {
        recommendation: "/api/recommendations/sample",
        validation: "/api/validations/sample",
        deployment: "/api/deployments/sample",
        failureImpact: "/api/failure-impacts/sample",
    };

    const { fetchAll } = window.ResilienceApi;
    const {
        setError,
        formatPercentage,
        formatRatioAsPercentage,
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

    function buildStat(label, value) {
        const wrapper = document.createElement("div");
        const labelEl = document.createElement("div");
        labelEl.className = "candidate-card__stat-label";
        labelEl.textContent = label;
        const valueEl = document.createElement("div");
        valueEl.className = "candidate-card__stat-value";
        valueEl.textContent = value;
        wrapper.append(labelEl, valueEl);
        return wrapper;
    }

    function renderCandidateCards(container, recommendation, failureImpact) {
        container.innerHTML = "";

        recommendation.recommendations.forEach((candidate) => {
            const isSelected = candidate.id === recommendation.selectedRecommendationId;

            const card = document.createElement("div");
            card.className = `candidate-card${isSelected ? " candidate-card--selected" : ""}`;

            const header = document.createElement("div");
            header.className = "candidate-card__header";

            const titleBlock = document.createElement("div");
            const title = document.createElement("div");
            title.className = "candidate-card__title";
            title.textContent = formatCodeLabel(candidate.type);
            const target = document.createElement("div");
            target.className = "candidate-card__target";
            target.textContent = `Target: ${candidate.service}`;
            titleBlock.append(title, target);

            const badges = document.createElement("div");
            badges.className = "stack-sm";
            const feasibleBadge = document.createElement("span");
            feasibleBadge.className = `status-badge status-badge--${
                candidate.feasible ? "success" : "danger"
            }`;
            feasibleBadge.textContent = candidate.feasible ? "Feasible" : "Not Feasible";
            badges.appendChild(feasibleBadge);
            if (isSelected) {
                const selectedBadge = document.createElement("span");
                selectedBadge.className = "status-badge status-badge--info";
                selectedBadge.style.marginLeft = "6px";
                selectedBadge.textContent = "Selected Recommendation";
                badges.appendChild(selectedBadge);
            }

            header.append(titleBlock, badges);

            const stats = document.createElement("div");
            stats.className = "candidate-card__stats";
            stats.appendChild(
                buildStat("Before", formatPercentage(failureImpact.blastRadius))
            );
            stats.appendChild(
                buildStat(
                    "Predicted After",
                    formatPercentage(candidate.predictedBlastRadius)
                )
            );
            stats.appendChild(
                buildStat(
                    "Risk Reduction",
                    `${candidate.riskReduction} pp`
                )
            );

            const secondaryStats = document.createElement("div");
            secondaryStats.className = "candidate-card__stats";
            secondaryStats.appendChild(buildStat("Resource Cost", candidate.resourceCost));
            secondaryStats.appendChild(
                buildStat("Confidence", formatRatioAsPercentage(candidate.confidence))
            );
            secondaryStats.appendChild(
                buildStat("Score", formatRatioAsPercentage(candidate.score))
            );

            card.append(header, stats, secondaryStats);
            container.appendChild(card);
        });
    }

    function renderCandidateTable(tbody, recommendation) {
        tbody.innerHTML = "";

        recommendation.recommendations.forEach((candidate) => {
            const row = document.createElement("tr");

            const nameCell = document.createElement("td");
            nameCell.textContent = formatCodeLabel(candidate.type);

            const feasibleCell = document.createElement("td");
            feasibleCell.textContent = candidate.feasible ? "Yes" : "No";

            const predictedCell = document.createElement("td");
            predictedCell.textContent = formatPercentage(candidate.predictedBlastRadius);

            const reductionCell = document.createElement("td");
            reductionCell.textContent = `${candidate.riskReduction} pp`;

            const costCell = document.createElement("td");
            costCell.textContent = candidate.resourceCost;

            const confidenceCell = document.createElement("td");
            confidenceCell.textContent = formatRatioAsPercentage(candidate.confidence);

            const scoreCell = document.createElement("td");
            scoreCell.textContent = formatRatioAsPercentage(candidate.score);

            row.append(
                nameCell,
                feasibleCell,
                predictedCell,
                reductionCell,
                costCell,
                confidenceCell,
                scoreCell
            );
            tbody.appendChild(row);
        });
    }

    function renderPlacement(currentContainer, simulatedContainer, deployment, candidate) {
        currentContainer.innerHTML = "";
        simulatedContainer.innerHTML = "";

        if (!candidate) return;

        const service = deployment.services.find(
            (item) => item.name === candidate.service
        );
        if (!service) return;

        service.pods.forEach((pod) => {
            const row = document.createElement("div");
            row.className = "placement-row";
            const name = document.createElement("span");
            name.textContent = pod.name;
            const node = document.createElement("span");
            node.textContent = pod.node;
            row.append(name, node);
            currentContainer.appendChild(row);
        });

        // Sample Virtual Placement: illustrates the round-robin spread the
        // real simulator performs server-side. Presentation only.
        service.pods.forEach((pod, index) => {
            const targetNode = deployment.nodes[index % deployment.nodes.length];
            const row = document.createElement("div");
            row.className = "placement-row";
            const name = document.createElement("span");
            name.textContent = pod.name;
            const node = document.createElement("span");
            node.textContent = targetNode.name;
            row.append(name, node);
            simulatedContainer.appendChild(row);
        });
    }

    function initYamlPanelToggle(region) {
        const panel = region.querySelector("#rec-raw-yaml");
        if (!panel) return;
        const toggle = panel.querySelector('[data-role="json-toggle"]');
        if (!toggle) return;

        toggle.addEventListener("click", () => {
            const isOpen = panel.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(isOpen));
            const label = toggle.querySelector('[data-role="json-toggle-label"]');
            if (label) {
                label.textContent = isOpen ? "Hide Generated YAML" : "View Generated YAML";
            }
        });
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;

        try {
            const data = await fetchAll(ENDPOINTS);
            const { recommendation, validation, deployment, failureImpact } = data;

            region.innerHTML = "";
            region.appendChild(template.content.cloneNode(true));

            const feasibleCandidates = recommendation.recommendations.filter(
                (candidate) => candidate.feasible
            );
            const bestPredicted = recommendation.recommendations.reduce(
                (best, candidate) =>
                    !best || candidate.predictedBlastRadius < best.predictedBlastRadius
                        ? candidate
                        : best,
                null
            );
            const selectedCandidate =
                recommendation.recommendations.find(
                    (candidate) => candidate.id === recommendation.selectedRecommendationId
                ) || recommendation.recommendations[0];

            setMetric(
                region,
                "rec-baseline-blast-radius",
                formatPercentage(failureImpact.blastRadius)
            );
            setMetric(
                region,
                "rec-best-predicted-blast-radius",
                bestPredicted ? formatPercentage(bestPredicted.predictedBlastRadius) : "—"
            );
            setMetric(
                region,
                "rec-risk-reduction",
                selectedCandidate ? `${selectedCandidate.riskReduction} pp` : "—"
            );
            setMetric(region, "rec-feasible-count", String(feasibleCandidates.length));

            renderCandidateCards(
                region.querySelector('[data-role="rec-candidate-list"]'),
                recommendation,
                failureImpact
            );
            renderCandidateTable(
                region.querySelector('[data-role="rec-candidate-table-body"]'),
                recommendation
            );
            renderPlacement(
                region.querySelector('[data-role="rec-placement-current"]'),
                region.querySelector('[data-role="rec-placement-simulated"]'),
                deployment,
                selectedCandidate
            );

            setMetric(
                region,
                "rec-predicted-blast-radius",
                formatPercentage(validation.predictedBlastRadius)
            );
            setMetric(
                region,
                "rec-observed-blast-radius",
                formatPercentage(validation.observedBlastRadius)
            );

            const predictedBar = region.querySelector('[data-role="rec-predicted-bar"]');
            if (predictedBar) {
                predictedBar.style.width = formatPercentage(validation.predictedBlastRadius);
            }
            const observedBar = region.querySelector('[data-role="rec-observed-bar"]');
            if (observedBar) {
                observedBar.style.width = formatPercentage(validation.observedBlastRadius);
            }

            setMetric(
                region,
                "rec-validation-explanation",
                `The observed result differs from the prediction by ${validation.predictionError} percentage points.`
            );

            const statusBadge = region.querySelector('[data-role="rec-validation-status"]');
            if (statusBadge) {
                statusBadge.className = `status-badge status-badge--${
                    validation.validated ? "success" : "danger"
                }`;
                statusBadge.textContent = `Validation ${
                    validation.validated ? "Passed" : "Failed"
                }`;
            }

            initJsonPanel(
                region.querySelector("#rec-raw-recommendation"),
                recommendation
            );
            initJsonPanel(region.querySelector("#rec-raw-validation"), validation);
            initYamlPanelToggle(region);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            setError(region, {
                endpoint: ENDPOINTS.recommendation,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load recommendation sample data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
