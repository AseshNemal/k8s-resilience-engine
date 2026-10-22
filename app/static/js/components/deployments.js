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

    // The evidence box: where and when the shown data was read, and a kubectl command to check it.
    function renderEvidence(body) {
        const box = document.querySelector('[data-role="evidence-box"]');
        if (!box) return;
        const put = (role, text) => {
            const node = box.querySelector(`[data-role="${role}"]`);
            if (node) node.textContent = text;
        };
        const graph = body.graph || {};
        const read = new Date(body.capturedAt || graph.timestamp);
        const evidence = Object.values(body.serviceEvidence || {});
        const ms = graph.validation?.runtimeOverheadMs;
        put("evidence-namespace", body.namespace);
        put("evidence-time", Number.isNaN(read.getTime()) ? "unknown" : read.toLocaleString());
        put("evidence-snapshot", graph.snapshotId || "n/a");
        put("evidence-took", ms == null ? "n/a" : `${(ms / 1000).toFixed(2)} s`);
        put("evidence-complete", `${evidence.filter((e) => e.complete).length} of ${evidence.length}`);
        put("evidence-command", `kubectl -n ${body.namespace} get pods -o wide`);
        put("evidence-line", `read at ${Number.isNaN(read.getTime()) ? "unknown time" : read.toLocaleTimeString()}`);
        box.hidden = false;
        // The main-output section names the same snapshot, so the JSON can be matched to what the page shows.
        document.querySelectorAll('[data-role="output-snapshot"]').forEach((n) => { n.textContent = graph.snapshotId || "n/a"; });
        document.querySelectorAll('[data-role="output-time"]').forEach((n) => {
            n.textContent = box.querySelector('[data-role="evidence-time"]').textContent;
        });
    }

    const readyCount = (s) => s.readyReplicas ?? s.ready_replicas ?? (s.pods ? s.pods.filter((p) => p.ready).length : 0);

    const exposedDomains = (s) => s.exposedFailureDomains ?? s.exposed_failure_domains ?? [];

    function renderHeadlineMetrics(deployment) {
        const services = deployment.services || [];
        // Exposure rate (placement_metrics.exposure_rate): exposed / evaluated, where services with 0 ready
        // replicas are UNAVAILABLE and not evaluated. Exposed = non-empty exposedFailureDomains.
        const evaluated = services.filter((s) => readyCount(s) > 0);
        const exposed = evaluated.filter((s) => exposedDomains(s).length > 0).length;
        const pods = services.reduce((acc, s) => acc + (s.pods || []).length, 0);

        setMetric("deployment-node-count", (deployment.nodes || []).length);
        setMetric("deployment-service-count", services.length);
        setMetric("deployment-ready-pods", `${services.reduce((acc, s) => acc + readyCount(s), 0)} / ${pods}`);
        setMetric(
            "deployment-exposed-services",
            evaluated.length ? `${exposed} / ${evaluated.length} (${ui().formatRatioAsPercentage(exposed / evaluated.length)})` : "n/a"
        );
    }

    // The sentence at the top of the page, from the same rule as the Exposed services tab.
    function verdictFor(services) {
        const evaluated = services.filter((s) => readyCount(s) > 0);
        const exposed = evaluated.filter((s) => exposedDomains(s).length > 0);
        const unavailable = services.length - evaluated.length;
        const downNote = unavailable ? ` ${unavailable} service(s) have no ready replica at all.` : "";
        if (!evaluated.length) {
            return { state: "muted", icon: "⏸️", title: "No ready service to assess", text: `No service in this namespace has a ready replica.${downNote}` };
        }
        if (!exposed.length) {
            return {
                state: "ok", icon: "✅",
                title: "No single node failure can take a service down",
                text: `All ${evaluated.length} services keep ready replicas on more than one node.${downNote}`,
            };
        }
        return {
            state: "danger", icon: "⚠️",
            title: `${exposed.length} of ${evaluated.length} services would go down if one node fails`,
            text: `Each has every ready replica on one node. Click one to see why.${downNote}`,
            names: exposed.map((s) => s.name),
        };
    }

    function goToTab(tab) {
        window.DeploymentsTabRouter?.switchTab(tab);
        document.querySelector(".dashboard-tabs-wrapper")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    // An exposed service's name opens its drill-down on the Exposed services tab.
    function nameChip(name) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "verdict__name";
        chip.textContent = name;
        chip.title = `Why is ${name} exposed?`;
        chip.addEventListener("click", () => {
            goToTab("drift");
            window.DeploymentsModalManager?.openViolationModal(name);
        });
        return chip;
    }

    function renderVerdict(verdict) {
        const box = document.querySelector('[data-role="verdict"]');
        if (!box) return;
        const put = (role, text) => {
            const node = box.querySelector(`[data-role="${role}"]`);
            if (node) node.textContent = text;
        };
        box.dataset.state = verdict.state;
        put("verdict-icon", verdict.icon);
        put("verdict-title", verdict.title);
        put("verdict-text", verdict.text);
        box.querySelector('[data-role="verdict-names"]')?.replaceChildren(...(verdict.names || []).map(nameChip));
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
            renderEvidence(body);

            renderHeadlineMetrics(deployment);
            const services = deployment.services || [];
            renderVerdict(verdictFor(services));

            if (window.DeploymentsTopologyWidget) {
                window.DeploymentsTopologyWidget.renderZoneGrid(deployment);
                window.DeploymentsTopologyWidget.renderConcentrationPanel(services);
            }

            window.DeploymentsDriftWidget?.renderSpreadMap(deployment);

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
            const box = document.querySelector('[data-role="evidence-box"]');
            if (box) box.hidden = true;  // no live data shown, so nothing to vouch for
            renderVerdict({
                state: "muted", icon: "⛔", title: "Could not read the cluster", text: "No verdict without live data; see the message above.",
            });
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
        document.querySelectorAll("[data-goto-tab]").forEach((btn) => {
            btn.addEventListener("click", () => goToTab(btn.dataset.gotoTab));
        });

        loadNamespaces()
            .then(loadPage)
            .catch((err) => console.error("Failed to load deployment data:", err));
    });
})();
