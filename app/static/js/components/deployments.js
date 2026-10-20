/**
 * Deployment Topology Modeling Master Dashboard Controller.
 * Every tab shows the same data source: the live cluster when it is reachable, otherwise the
 * sample fixture, clearly labelled. Live-only views say so instead of mixing in live data.
 * Component: Component 2 - Deployment Topology Modeling
 * Owned by: Member 2 (P.A.M. Minara)
 */
(function () {
    "use strict";

    const LIVE = "/api/deployments/live";
    const SAMPLE = "/api/deployments/sample";
    let currentEndpoint = SAMPLE;
    const { formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi || {
            formatRatioAsPercentage: (r) => `${Math.round(r * 100)}%`,
            initJsonPanel: () => { },
            updateLastRefreshed: () => { },
        };

    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const toggleSourceButton = document.querySelector('[data-role="toggle-source-button"]');
    const sourceBadge = document.querySelector('[data-role="source-badge"]');
    const sourceBanner = document.querySelector('[data-role="source-banner"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    const isLive = () => currentEndpoint === LIVE;

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function showSource(note) {
        if (sourceBadge) {
            sourceBadge.textContent = isLive() ? "Live Cluster (KinD)" : "Sample Data";
            sourceBadge.className = `status-badge ${isLive() ? "status-badge--success" : "status-badge--muted"} status-badge--no-dot`;
        }
        if (toggleSourceButton) {
            toggleSourceButton.textContent = isLive() ? "Switch to Sample Data" : "Switch to Live Cluster (KinD)";
        }
        // The shared top bar always says "Sample Data"; on this page it should name the real source.
        const topbarSource = document.querySelector(".pill-badge--teal");
        if (topbarSource && topbarSource.lastChild) {
            topbarSource.lastChild.textContent = isLive() ? " Live Cluster" : " Sample Data";
        }
        if (sourceBanner) {
            sourceBanner.className = `ui-note source-banner ${isLive() ? "source-banner--live" : "source-banner--sample"}`;
            sourceBanner.textContent = note || (isLive()
                ? "Showing the live cluster of the current kubectl context. Every tab uses this same data."
                : "Showing the SAMPLE fixture (sample_data/deployment.json), not a real cluster. Live-only views are disabled.");
        }
    }

    async function loadPage(note) {
        if (reloadButton) reloadButton.disabled = true;
        if (toggleSourceButton) toggleSourceButton.disabled = true;

        try {
            const resp = await fetch(currentEndpoint);
            if (!resp.ok) {
                const body = await resp.json().catch(() => ({}));
                if (isLive()) {
                    // Cluster unreachable: say so and show the labelled sample instead of stale live data.
                    currentEndpoint = SAMPLE;
                    return loadPage(`No Kubernetes cluster reachable (${body.detail || resp.status}). Showing the SAMPLE fixture instead.`);
                }
                throw new Error(body.detail || `HTTP ${resp.status}`);
            }
            const deployment = await resp.json();
            showSource(note);

            // Headline metrics
            const services = deployment.services || [];
            const ready = (s) => s.readyReplicas ?? s.ready_replicas ?? (s.pods ? s.pods.filter((p) => p.ready).length : 0);
            const readyPods = services.reduce((acc, s) => acc + ready(s), 0);

            // Exposure rate (placement_metrics.exposure_rate): exposed / evaluated, where services with
            // 0 ready replicas are UNAVAILABLE and not evaluated. Exposed = non-empty exposedFailureDomains.
            const evaluated = services.filter((s) => ready(s) > 0);
            const exposed = evaluated.filter((s) => (s.exposedFailureDomains ?? s.exposed_failure_domains ?? []).length > 0).length;

            setMetric("deployment-node-count", (deployment.nodes || []).length);
            setMetric("deployment-service-count", services.length);
            setMetric("deployment-ready-pods", readyPods);
            setMetric(
                "deployment-exposed-services",
                evaluated.length ? `${exposed} / ${evaluated.length} (${formatRatioAsPercentage(exposed / evaluated.length)})` : "n/a"
            );

            if (window.DeploymentsTopologyWidget) {
                window.DeploymentsTopologyWidget.renderZoneGrid(deployment);
                window.DeploymentsTopologyWidget.renderConcentrationPanel(services);
            }
            const rawPanelEl = document.querySelector("#deployment-raw-json");
            if (rawPanelEl) initJsonPanel(rawPanelEl, deployment);

            // Intent and drift tabs fetch their own data; load them in parallel.
            await Promise.all([
                window.DeploymentsIntentWidget?.loadIntentCompliance(services, isLive()),
                window.DeploymentsDriftWidget?.loadDriftSignals(isLive()),
            ]);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            console.error("Failed to load deployment data:", error);
            if (sourceBanner) sourceBanner.textContent = `Could not load deployment data: ${error.message}`;
        } finally {
            if (reloadButton) reloadButton.disabled = false;
            if (toggleSourceButton) toggleSourceButton.disabled = false;
        }
    }

    const reload = (note) => loadPage(note).catch((err) => console.error("Failed to load deployment data:", err));

    if (toggleSourceButton) {
        toggleSourceButton.addEventListener("click", () => {
            currentEndpoint = isLive() ? SAMPLE : LIVE;
            reload();
        });
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", () => reload());
    }

    document.addEventListener("DOMContentLoaded", () => {
        if (window.DeploymentsTabRouter) window.DeploymentsTabRouter.init();
        if (window.DeploymentsAcademicWidget) window.DeploymentsAcademicWidget.init();
        if (window.DeploymentsTopologyWidget) window.DeploymentsTopologyWidget.init();
        if (window.DeploymentsIntentWidget) window.DeploymentsIntentWidget.init();
        if (window.DeploymentsLiveExperimentsWidget) window.DeploymentsLiveExperimentsWidget.init();
        if (window.DeploymentsModalManager) window.DeploymentsModalManager.init();

        // Start on the live cluster; loadPage falls back to the labelled sample if it is unreachable.
        currentEndpoint = LIVE;
        reload();
    });
})();
