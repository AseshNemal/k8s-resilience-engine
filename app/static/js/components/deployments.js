/**
 * Deployment Topology Modeling Master Dashboard Controller.
 * Phase 5 Executive Dashboard & Academic Validation UI.
 * Component: Component 2 - Deployment Topology Modeling
 * Owned by: Member 2 (P.A.M. Minara)
 */
(function () {
    "use strict";

    let currentEndpoint = "/api/deployments/sample";
    const { fetchJson } = window.ResilienceApi || { fetchJson: (url) => fetch(url).then((r) => r.json()) };
    const { formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi || {
            formatRatioAsPercentage: (r) => `${Math.round(r * 100)}%`,
            initJsonPanel: () => { },
            updateLastRefreshed: () => { },
        };

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
            const deployment = await fetchJson(currentEndpoint);

            // 1. Top Executive Metrics
            const totalNodes = deployment.nodes ? deployment.nodes.length : 0;
            const totalServices = deployment.services ? deployment.services.length : 0;
            const readyPods = deployment.services
                ? deployment.services.reduce((acc, s) => acc + (s.readyReplicas ?? s.ready_replicas ?? (s.pods ? s.pods.filter((p) => p.ready).length : 0)), 0)
                : 0;

            let highestConcentration = 0;
            if (deployment.services && deployment.services.length > 0) {
                highestConcentration = Math.max(
                    ...deployment.services.map((s) => s.replicaConcentration ?? s.replica_concentration ?? 0.0)
                );
            }

            setMetric("deployment-node-count", totalNodes);
            setMetric("deployment-service-count", totalServices);
            setMetric("deployment-ready-pods", readyPods);
            setMetric("deployment-highest-concentration", formatRatioAsPercentage(highestConcentration));

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
        } finally {
            if (reloadButton) reloadButton.disabled = false;
            if (toggleSourceButton) toggleSourceButton.disabled = false;
        }
    }

    // Toggle between Live KinD cluster and Sample baseline
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
