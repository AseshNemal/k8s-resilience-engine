/*
 * Overview dashboard page script.
 * Shared page (no single member owns Overview) — coordinate before editing.
 *
 * Only reads values already returned by the sample APIs (node count,
 * blastRadius, survivability, recommendation fields, etc.) and formats them
 * for display. No blast-radius, survivability, or score calculation happens
 * here.
 */

(function () {
    const { fetchAll } = window.ResilienceApi;
    const {
        setError,
        formatPercentage,
        formatCodeLabel,
        updateLastRefreshed,
    } = window.ResilienceUi;

    const ENDPOINTS = {
        workflow: "/api/workflows/sample",
        deployment: "/api/deployments/sample",
        failureImpact: "/api/failure-impacts/sample",
        recommendation: "/api/recommendations/sample",
    };

    const errorRegion = document.querySelector('[data-role="overview-error-region"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function setRole(name, mutate) {
        const element = document.querySelector(`[data-role="${name}"]`);
        if (element) mutate(element);
    }

    // Same UI-display thresholds already used on the Failure Impact page —
    // presentation labels only, not a research classification.
    function blastRadiusTier(value) {
        if (value >= 80) return { text: "Critical", variant: "danger" };
        if (value >= 50) return { text: "High", variant: "warning" };
        return { text: "Low", variant: "success" };
    }

    // Survivability is "higher is better", so the tier direction is inverted
    // relative to blast radius, using the same three thresholds.
    function survivabilityTier(value) {
        if (value >= 80) return { text: "High", variant: "success" };
        if (value >= 50) return { text: "Moderate", variant: "warning" };
        return { text: "Low", variant: "danger" };
    }

    async function loadOverview() {
        if (errorRegion) errorRegion.innerHTML = "";
        if (reloadButton) reloadButton.disabled = true;

        try {
            const data = await fetchAll(ENDPOINTS);

            // UI summaries only — these are direct reads of API fields.
            setMetric("workflows-modelled", "1");
            setMetric("cluster-nodes", String(data.deployment.nodes.length));
            setMetric(
                "highest-blast-radius",
                formatPercentage(data.failureImpact.blastRadius)
            );
            setMetric(
                "recommendations-generated",
                String(data.recommendation.recommendations.length)
            );

            const topTier = blastRadiusTier(data.failureImpact.blastRadius);
            setRole("blast-radius-tag", (el) => {
                el.className = `status-badge status-badge--${topTier.variant}`;
                el.textContent = topTier.text;
            });

            const selectedRecommendation =
                data.recommendation.recommendations.find(
                    (item) => item.id === data.recommendation.selectedRecommendationId
                ) || data.recommendation.recommendations[0];

            setMetric("analysis-workflow", data.workflow.workflowId);
            setMetric("analysis-failed-node", data.failureImpact.failedNode);
            setMetric(
                "analysis-blast-radius",
                formatPercentage(data.failureImpact.blastRadius)
            );
            setMetric(
                "analysis-survivability",
                formatPercentage(data.failureImpact.survivability)
            );
            setMetric(
                "analysis-recommended-action",
                selectedRecommendation
                    ? formatCodeLabel(selectedRecommendation.type)
                    : "—"
            );
            setMetric(
                "analysis-predicted-blast-radius",
                selectedRecommendation
                    ? formatPercentage(selectedRecommendation.predictedBlastRadius)
                    : "—"
            );

            const blastTier = blastRadiusTier(data.failureImpact.blastRadius);
            setRole("analysis-blast-radius-bar", (el) => {
                el.style.width = formatPercentage(data.failureImpact.blastRadius);
            });
            setRole("analysis-blast-radius-tag", (el) => {
                el.className = `status-badge status-badge--${blastTier.variant} analysis-card__tag`;
                el.textContent = blastTier.text;
            });

            const survivalTier = survivabilityTier(data.failureImpact.survivability);
            setRole("analysis-survivability-bar", (el) => {
                el.style.width = formatPercentage(data.failureImpact.survivability);
            });
            setRole("analysis-survivability-tag", (el) => {
                el.className = `status-badge status-badge--${survivalTier.variant} analysis-card__tag`;
                el.textContent = survivalTier.text;
            });

            if (selectedRecommendation) {
                const predictedTier = blastRadiusTier(
                    selectedRecommendation.predictedBlastRadius
                );
                setRole("analysis-predicted-bar", (el) => {
                    el.style.width = formatPercentage(
                        selectedRecommendation.predictedBlastRadius
                    );
                });
                setRole("analysis-predicted-tag", (el) => {
                    el.className = `status-badge status-badge--${predictedTier.variant} analysis-card__tag`;
                    el.textContent = predictedTier.text;
                });
            }

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            setError(errorRegion, {
                message: error.message,
                onRetry: loadOverview,
            });
            console.error("Failed to load overview sample data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadOverview);
    }

    document.addEventListener("DOMContentLoaded", loadOverview);
})();
