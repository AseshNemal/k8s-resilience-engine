/*
 * Overview dashboard page script.
 * Shared page (no single member owns Overview) — coordinate before editing.
 *
 * Shows only what the connected cluster reported through /api/deployments/discovery/all: every namespace
 * that holds workloads, with its services, replicas and nodes. When the cluster cannot be read the page
 * shows that failure; it never displays placeholder or estimated figures.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { updateLastRefreshed } = window.ResilienceUi;
    const { describeError, describeOverview, renderStateCard, applyBadge } = window.ResilienceConnection;

    const errorRegion = document.querySelector('[data-role="overview-error-region"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const badge = document.querySelector('[data-role="source-badge"]');
    const summary = document.querySelector('[data-role="discovery-summary"]');
    const namespacesRegion = document.querySelector('[data-role="overview-namespaces"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(name, value) {
        const element = document.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text; // never parsed as HTML
        return node;
    }

    function clearFigures() {
        setMetric("cluster-nodes", "—");
        setMetric("services-discovered", "—");
        setMetric("unresolved-items", "—");
        if (summary) summary.hidden = true;
        if (namespacesRegion) namespacesRegion.replaceChildren();
    }

    function showSummary(described) {
        const headline = document.querySelector('[data-role="discovery-headline"]');
        const notes = document.querySelector('[data-role="discovery-notes"]');
        const provenance = document.querySelector('[data-role="discovery-provenance"]');
        if (headline) headline.textContent = described.headline;
        if (provenance) provenance.textContent = described.provenance;
        if (notes) {
            notes.replaceChildren(...described.notes.map((note) => make("li", "", note)));
        }
        if (summary) summary.hidden = false;
    }

    function serviceTable(services) {
        const table = make("table", "ns-table");
        const head = make("tr");
        for (const title of ["Service", "Replicas", "Runs on"]) head.append(make("th", "", title));
        const thead = make("thead");
        thead.append(head);
        table.append(thead);
        const body = make("tbody");
        for (const service of services) {
            const row = make("tr");
            row.append(make("td", "", service.name), make("td", "", service.replicas),
                make("td", "", service.nodes.join(", ")));
            body.append(row);
        }
        table.append(body);
        return table;
    }

    function namespaceCard(row) {
        const card = make("details", "ns-card");
        card.open = true;
        const head = make("summary", "ns-card__summary");
        const state = make("span");
        applyBadge(state, row.badge);
        head.append(make("strong", "ns-card__name", row.namespace), state,
            make("span", "ns-card__counts", `${row.services.length} services · ${row.unresolved} unresolved`));
        card.append(head);
        if (row.services.length) {
            card.append(serviceTable(row.services));
        } else {
            card.append(make("p", "ns-card__empty", "No services were resolved in this namespace."));
        }
        if (row.unresolvedNotes.length) {
            const list = make("ul", "ns-card__unresolved");
            list.append(...row.unresolvedNotes.map((note) => make("li", "", `Unresolved ${note}`)));
            card.append(list);
        }
        return card;
    }

    async function loadOverview() {
        if (errorRegion) errorRegion.replaceChildren();
        if (reloadButton) reloadButton.disabled = true;
        // Never leave the previous cluster's figures on screen while (or after) a refresh fails.
        clearFigures();
        applyBadge(badge, { variant: "muted", text: "Loading…" });

        try {
            const result = await fetchJson("/api/deployments/discovery/all");
            const described = describeOverview(result);
            applyBadge(badge, described.badge);
            setMetric("cluster-nodes", String(described.totals.workerNodes));
            setMetric("services-discovered", String(described.totals.services));
            setMetric("unresolved-items", String(described.totals.unresolved));
            showSummary(described);
            if (namespacesRegion) namespacesRegion.replaceChildren(...described.rows.map(namespaceCard));
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
