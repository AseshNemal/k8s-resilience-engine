/*
 * Overview dashboard page script.
 * Shared page (no single member owns Overview) — coordinate before editing.
 *
 * Shows only what the connected cluster reported through /api/deployments/discovery. When the cluster
 * cannot be read the page shows that failure; it never displays placeholder or estimated figures.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { updateLastRefreshed } = window.ResilienceUi;
    const { describeError, describeDiscovery, renderStateCard, applyBadge } = window.ResilienceConnection;

    const errorRegion = document.querySelector('[data-role="overview-error-region"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const namespaceInput = document.querySelector('[data-role="namespace"]');
    const badge = document.querySelector('[data-role="source-badge"]');
    const summary = document.querySelector('[data-role="discovery-summary"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function clearFigures() {
        setMetric("cluster-nodes", "—");
        setMetric("services-discovered", "—");
        setMetric("unresolved-items", "—");
        if (summary) summary.hidden = true;
    }

    function showSummary(described) {
        const headline = document.querySelector('[data-role="discovery-headline"]');
        const notes = document.querySelector('[data-role="discovery-notes"]');
        const provenance = document.querySelector('[data-role="discovery-provenance"]');
        if (headline) headline.textContent = described.headline;
        if (provenance) provenance.textContent = described.provenance;
        if (notes) {
            notes.replaceChildren(...described.notes.map((note) => {
                const item = document.createElement("li");
                item.textContent = note;
                return item;
            }));
        }
        if (summary) summary.hidden = false;
    }

    async function loadOverview() {
        if (errorRegion) errorRegion.replaceChildren();
        if (reloadButton) reloadButton.disabled = true;
        // Never leave the previous cluster's figures on screen while (or after) a refresh fails.
        clearFigures();
        applyBadge(badge, { variant: "muted", text: "Loading…" });

        const namespace = (namespaceInput && namespaceInput.value.trim()) || "default";
        try {
            const result = await fetchJson(`/api/deployments/discovery?namespace=${encodeURIComponent(namespace)}`);
            const described = describeDiscovery(result);
            applyBadge(badge, described.badge);
            setMetric("cluster-nodes", String(result.graph.nodes.length));
            setMetric("services-discovered", String(result.graph.services.length));
            setMetric("unresolved-items", String(result.unresolved.length));
            showSummary(described);
            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            applyBadge(badge, { variant: "danger", text: "Not showing live data" });
            renderStateCard(errorRegion, describeError(error), { onRetry: loadOverview });
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadOverview);
    }

    document.addEventListener("DOMContentLoaded", loadOverview);
})();
