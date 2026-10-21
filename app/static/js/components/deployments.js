/**
 * Deployment Topology Modeling Master Dashboard Controller.
 * Every tab shows the connected cluster; there is no sample baseline to switch to. When the cluster
 * cannot be read, the page says why (failure state below) and shows no topology.
 * Component: Component 2 - Deployment Topology Modeling
 * Owned by: Member 2 (P.A.M. Minara)
 */
(function () {
    "use strict";

    const LIVE_ENDPOINT = "/api/deployments/discovery";
    const NAMESPACES_ENDPOINT = "/api/deployments/discovery/all";

    // base.html loads api.js, ui.js and connection-state.js with `defer`, so they run after this script.
    // Read them when the page loads (DOMContentLoaded), not at the top of this file.
    const api = () => window.ResilienceApi || { fetchJson: (url) => fetch(url).then((r) => r.json()) };
    const ui = () => window.ResilienceUi || {
        formatRatioAsPercentage: (r) => `${Math.round(r * 100)}%`,
        initJsonPanel: () => { },
        updateLastRefreshed: () => { },
    };
    // Explicit failure states: when the cluster cannot be read the page says why and offers a retry,
    // rather than leaving the widgets empty. See app/static/js/connection-state.js.
    const connection = () => window.ResilienceConnection || {};

    const region = document.querySelector('[data-role="page-region"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const sourceBadge = document.querySelector('[data-role="source-badge"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');
    const namespacePicker = document.querySelector('[data-role="namespace-select"]');
    const scope = () => window.DeploymentsNamespace;

    function namespaceOption(row) {
        const option = document.createElement("option");
        option.value = row.namespace;
        option.textContent = `${row.namespace} · ${(row.graph?.services || []).length} services`;
        return option;
    }

    // Fills the header selector with the namespaces that hold workloads and picks the one to show.
    // If the list cannot be read, the page still loads "default" (or the URL's namespace) and loadPage
    // shows the cluster failure itself.
    async function loadNamespaces() {
        let rows = [];
        try {
            rows = (await api().fetchJson(NAMESPACES_ENDPOINT)).namespaces || [];
        } catch (error) {
            console.warn("Could not list the cluster's namespaces:", error);
        }
        const requested = scope().requested();
        const chosen = rows.length ? scope().choose(rows, requested) : requested || scope().get();
        if (namespacePicker) {
            const listed = rows.some((row) => row.namespace === chosen) ? rows : [...rows, { namespace: chosen }];
            namespacePicker.replaceChildren(...listed.map(namespaceOption));
            namespacePicker.value = chosen;
            namespacePicker.disabled = false;
        }
        scope().set(chosen);
    }

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    const readyCount = (s) => s.readyReplicas ?? s.ready_replicas ?? (s.pods ? s.pods.filter((p) => p.ready).length : 0);

    function renderHeadlineMetrics(deployment) {
        const services = deployment.services || [];
        // Exposure rate (placement_metrics.exposure_rate): exposed / evaluated, where services with 0 ready
        // replicas are UNAVAILABLE and not evaluated. Exposed = non-empty exposedFailureDomains.
        const evaluated = services.filter((s) => readyCount(s) > 0);
        const exposed = evaluated.filter((s) => (s.exposedFailureDomains ?? s.exposed_failure_domains ?? []).length > 0).length;

        setMetric("deployment-node-count", (deployment.nodes || []).length);
        setMetric("deployment-service-count", services.length);
        setMetric("deployment-ready-pods", services.reduce((acc, s) => acc + readyCount(s), 0));
        setMetric(
            "deployment-exposed-services",
            evaluated.length ? `${exposed} / ${evaluated.length} (${ui().formatRatioAsPercentage(exposed / evaluated.length)})` : "n/a"
        );
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;

        const { describeError, describeDiscovery, renderStateCard, applyBadge } = connection();
        const { initJsonPanel, updateLastRefreshed } = ui();
        try {
            // The discovery envelope: the graph plus how complete it is (ok / empty / partial).
            const body = await api().fetchJson(scope().withNamespace(LIVE_ENDPOINT));
            const deployment = body.graph;
            if (region) region.replaceChildren();
            if (applyBadge && describeDiscovery) {
                applyBadge(sourceBadge, describeDiscovery(body).badge);
            }

            renderHeadlineMetrics(deployment);
            const services = deployment.services || [];

            if (window.DeploymentsTopologyWidget) {
                window.DeploymentsTopologyWidget.renderZoneGrid(deployment);
                window.DeploymentsTopologyWidget.renderConcentrationPanel(services);
            }

            const rawPanelEl = document.querySelector("#deployment-raw-json");
            if (rawPanelEl) initJsonPanel(rawPanelEl, deployment);

            // Intent and drift tabs fetch their own data from the same cluster; load them in parallel.
            await Promise.all([
                window.DeploymentsIntentWidget?.loadIntentCompliance(services),
                window.DeploymentsDriftWidget?.loadDriftSignals(),
            ]);

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            console.error("Failed to load deployment data:", error);
            if (applyBadge && renderStateCard && describeError && region) {
                applyBadge(sourceBadge, { variant: "danger", text: "Not showing live data" });
                renderStateCard(region, describeError(error), { onRetry: loadPage });
            }
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", () => {
            loadPage().catch((err) => console.error("Failed to load deployment data:", err));
        });
    }

    if (namespacePicker) {
        namespacePicker.addEventListener("change", () => {
            scope().set(namespacePicker.value);
            loadPage().catch((err) => console.error("Failed to load deployment data:", err));
        });
    }

    document.addEventListener("DOMContentLoaded", () => {
        if (window.DeploymentsTabRouter) window.DeploymentsTabRouter.init();
        if (window.DeploymentsAcademicWidget) window.DeploymentsAcademicWidget.init();
        if (window.DeploymentsTopologyWidget) window.DeploymentsTopologyWidget.init();
        if (window.DeploymentsIntentWidget) window.DeploymentsIntentWidget.init();
        if (window.DeploymentsLiveExperimentsWidget) window.DeploymentsLiveExperimentsWidget.init();
        if (window.DeploymentsModalManager) window.DeploymentsModalManager.init();

        loadNamespaces()
            .then(loadPage)
            .catch((err) => console.error("Failed to load deployment data:", err));
    });
})();
