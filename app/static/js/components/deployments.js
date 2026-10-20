/**
 * Deployment Topology Modeling Master Dashboard Controller.
 * Phase 5 Executive Dashboard & Academic Validation UI.
 * Component: Component 2 - Deployment Topology Modeling
 * Owned by: Member 2 (P.A.M. Minara)
 */
(function () {
    "use strict";

    // Live discovery is the default source. The sample baseline (Member 2's original toggle) is still one
    // click away, and the page labels it as sample data whenever it is showing.
    const LIVE_ENDPOINT = "/api/deployments/discovery";
    const SAMPLE_ENDPOINT = "/api/deployments/sample";
    let currentEndpoint = LIVE_ENDPOINT;
    const { fetchJson } = window.ResilienceApi || { fetchJson: (url) => fetch(url).then((r) => r.json()) };
    const { formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi || {
            formatRatioAsPercentage: (r) => `${Math.round(r * 100)}%`,
            initJsonPanel: () => { },
            updateLastRefreshed: () => { },
        };

    // Explicit failure states (our addition): when the cluster cannot be read the page says why and offers a
    // retry, rather than leaving the widgets empty. See app/static/js/connection-state.js.
    const { describeError, describeDiscovery, renderStateCard, applyBadge } = window.ResilienceConnection || {};

    const region = document.querySelector('[data-role="page-region"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const toggleSourceButton = document.querySelector('[data-role="toggle-source-button"]');
    const sourceBadge = document.querySelector('[data-role="source-badge"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;
        if (toggleSourceButton) toggleSourceButton.disabled = true;

        try {
            const body = await fetchJson(currentEndpoint);
            const live = currentEndpoint === LIVE_ENDPOINT;
            // The live source returns the graph inside a status envelope (ok / empty / partial); the
            // sample source returns the bare graph.
            const deployment = live ? body.graph : body;
            if (region) region.replaceChildren();
            if (live && applyBadge && describeDiscovery) {
                const described = describeDiscovery(body);
                applyBadge(sourceBadge, described.badge);
            }

            // 1. Top Executive Metrics
            const totalNodes = deployment.nodes ? deployment.nodes.length : 0;
            const totalServices = deployment.services ? deployment.services.length : 0;
            const readyPods = deployment.services
                ? deployment.services.reduce((acc, s) => acc + (s.readyReplicas ?? s.ready_replicas ?? (s.pods ? s.pods.filter((p) => p.ready).length : 0)), 0)
                : 0;

            // A service whose concentration the cluster did not report is left out, not counted as 0%.
            let highestConcentration = null;
            const reported = (deployment.services || [])
                .map((s) => s.replicaConcentration ?? s.replica_concentration)
                .filter((value) => value !== null && value !== undefined);
            if (reported.length > 0) {
                highestConcentration = Math.max(...reported);
            }

            setMetric("deployment-node-count", totalNodes);
            setMetric("deployment-service-count", totalServices);
            setMetric("deployment-ready-pods", readyPods);
            setMetric(
                "deployment-highest-concentration",
                highestConcentration === null ? "—" : formatRatioAsPercentage(highestConcentration)
            );

            // 2. Delegate to Sub-Widgets
            if (window.DeploymentsTopologyWidget) {
                window.DeploymentsTopologyWidget.renderZoneGrid(deployment);
                window.DeploymentsTopologyWidget.renderConcentrationPanel(deployment.services);
            }

            if (window.DeploymentsIntentWidget) {
                window.DeploymentsIntentWidget.loadIntentCompliance(deployment.services);
            }

            if (window.DeploymentsDriftWidget) {
                window.DeploymentsDriftWidget.loadDriftSignals();
            }

            // 3. Raw JSON Panel
            const rawPanelEl = document.querySelector("#deployment-raw-json");
            if (rawPanelEl) {
                initJsonPanel(rawPanelEl, deployment);
            }

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            console.error("Failed to load deployment data:", error);
            if (applyBadge && renderStateCard && describeError && region) {
                applyBadge(sourceBadge, { variant: "danger", text: "Not showing live data" });
                renderStateCard(region, describeError(error), { onRetry: loadPage });
            }
        } finally {
            if (reloadButton) reloadButton.disabled = false;
            if (toggleSourceButton) toggleSourceButton.disabled = false;
        }
    }

    // Toggle between the live cluster and the sample baseline. The sample is a fixed research fixture
    // and is labelled as such; it is never shown without the person asking for it.
    if (toggleSourceButton) {
        toggleSourceButton.addEventListener("click", () => {
            if (currentEndpoint === LIVE_ENDPOINT) {
                currentEndpoint = SAMPLE_ENDPOINT;
                if (sourceBadge) {
                    sourceBadge.textContent = "Sample Data";
                    sourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
                }
                toggleSourceButton.textContent = "Switch to Live Cluster";
            } else {
                currentEndpoint = LIVE_ENDPOINT;
                toggleSourceButton.textContent = "Switch to Sample Data";
            }
            loadPage();
        });
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", () => {
        // Initialize all Phase 5 modular widgets
        if (window.DeploymentsTabRouter) window.DeploymentsTabRouter.init();
        if (window.DeploymentsAcademicWidget) window.DeploymentsAcademicWidget.init();
        if (window.DeploymentsTopologyWidget) window.DeploymentsTopologyWidget.init();
        if (window.DeploymentsIntentWidget) window.DeploymentsIntentWidget.init();
        if (window.DeploymentsDriftWidget) window.DeploymentsDriftWidget.init();
        if (window.DeploymentsScenariosWidget) window.DeploymentsScenariosWidget.init();
        if (window.DeploymentsModalManager) window.DeploymentsModalManager.init();

        loadPage();
    });
})();
