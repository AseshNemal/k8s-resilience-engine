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

    // ---- The research summary: the cluster is not the result ---------------------------------------------------------------
    // Everything below is read from what the backend stores or computes (/api/recommendations/...). A request that fails leaves its part
    // saying so, with the backend's own reason; nothing is filled in. Each part is drawn as soon as its own answer arrives.
    const OV = window.ResilienceOverview;
    const sections = {
        applied: document.querySelector('[data-role="overview-applied"]'),
        appliedText: document.querySelector('[data-role="overview-applied-text"]'),
        risk: document.querySelector('[data-role="overview-risk"]'),
        riskHeadline: document.querySelector('[data-role="overview-risk-headline"]'),
        riskSub: document.querySelector('[data-role="overview-risk-sub"]'),
        results: document.querySelector('[data-role="overview-results"]'),
        placement: document.querySelector('[data-role="overview-placement"]'),
        evidence: document.querySelector('[data-role="overview-evidence"]'),
    };
    const STATE_WORD = { available: "Ready", missing: "Needed", incomplete: "Partial", pending: "Checking" };
    let researchRun = 0;

    // The links are in the page itself (so the extension build can point them at its own files); this fills in each one's state and detail.
    function drawStrip(chips) {
        for (const chip of chips) {
            const link = document.querySelector(`[data-role="overview-chip-${chip.id}"]`);
            const state = document.querySelector(`[data-role="overview-chip-${chip.id}-state"]`);
            const detail = document.querySelector(`[data-role="overview-chip-${chip.id}-detail"]`);
            if (link) link.setAttribute("data-state", chip.state);
            if (state) state.textContent = STATE_WORD[chip.state] || chip.state;
            if (detail) detail.textContent = chip.detail;
        }
    }

    function drawRisk(risk) {
        if (!sections.risk) return;
        sections.risk.setAttribute("data-state", risk.state);
        sections.risk.setAttribute("data-lost", String(risk.state === "available" && risk.survives === false));
        if (sections.riskHeadline) sections.riskHeadline.textContent = risk.headline;
        if (sections.riskSub) sections.riskSub.textContent = risk.sub;
    }

    function drawApplied(applied) {
        if (!sections.applied) return;
        sections.applied.hidden = !applied.visible;
        if (applied.visible && sections.appliedText) sections.appliedText.textContent = applied.text;
    }

    function statePill(state, label) {
        const node = make("span", "overview-badge", label);
        node.setAttribute("data-state", state);
        return node;
    }

    function table(headers, rows) {
        const wrap = make("table");
        const head = make("tr");
        headers.forEach((title) => head.append(make("th", "", title)));
        const thead = make("thead");
        thead.append(head);
        const body = make("tbody");
        rows.forEach((cells) => {
            const row = make("tr");
            cells.forEach((cell) => { const td = make("td"); if (typeof cell === "string") td.textContent = cell; else td.append(cell); row.append(td); });
            body.append(row);
        });
        wrap.append(thead, body);
        return wrap;
    }

    function drawResults(rows) {
        if (!sections.results) return;
        if (!rows.length) {
            sections.results.replaceChildren(make("p", "ui-note", "No fix has been applied and tested yet. Apply one on the Recommendations page."));
            return;
        }
        sections.results.replaceChildren(table(["Fix", "Application", "Risk before", "Predicted", "After rollout", "Test outcome", "Date (UTC)"],
            rows.map((row) => [row.name, row.namespace, row.baseline, row.predicted, row.after, statePill(row.outcomeState, row.outcome), row.when])));
    }

    function drawEvidence(items) {
        if (!sections.evidence) return;
        sections.evidence.replaceChildren(table(["Evidence", "State", "Detail"],
            items.map((item) => [item.label, statePill(item.state, STATE_WORD[item.state] || item.state), item.detail])));
    }

    function drawPlacement(detail, reason) {
        if (!sections.placement) return;
        const compare = window.ResilienceTopologyCompare;
        if (!detail || !compare) {
            sections.placement.replaceChildren(make("p", "ui-note", reason || "No recommendation was computed, so there is no placement to compare."));
            return;
        }
        const selected = Array.isArray(detail.candidates) ? detail.candidates.find((candidate) => candidate.selected) : null;
        sections.placement.replaceChildren();
        compare.render(sections.placement, detail.currentDeployment, selected ? selected.simulatedDeployment : null,
            { failedNode: (detail.evidence && detail.evidence.failedNode) || null });
    }

    async function attempt(url, options) {
        try { return { body: await fetchJson(url, options) }; } catch (error) { return { error }; }
    }

    function detailUrl(context) {
        if (!context || !context.namespace) return null;
        const frozen = (context.workflows || []).some((w) => w.applicable);
        const declared = (context.declaredWorkflows || []).some((w) => w.applicable);
        if (!frozen && !declared) return null;
        return `/api/recommendations/${frozen ? "live" : "declared"}/detail?namespace=${encodeURIComponent(context.namespace)}&all_workflows=true`;
    }

    async function loadResearch() {
        if (!OV) return;
        const run = ++researchRun;
        const current = () => run === researchRun;
        const state = { discovery: true, context: undefined, risk: undefined, history: undefined };
        const showStrip = () => drawStrip(OV.describeChips(state));
        drawRisk(OV.describeRisk(null, { pending: true }));
        drawPlacement(null, "Waiting for the recommendation…");
        showStrip();

        const contextRequest = attempt("/api/recommendations/context");
        const historyRequest = attempt("/api/recommendations/apply-tests/history?limit=50");
        const appliedRequest = attempt("/api/recommendations/applications/current");

        appliedRequest.then((result) => { if (current()) drawApplied(OV.describeApplied(result.body)); });
        historyRequest.then((result) => {
            if (!current()) return;
            state.history = result.body || null;
            drawResults(OV.describeResults(state.history));
            showStrip();
        });
        const context = await contextRequest;
        if (!current()) return;
        state.context = context.body || { workflows: [], declaredWorkflows: [], evidence: [{ id: "workflow", detail: context.error ? context.error.message : "Not available" }] };
        showStrip();
        await historyRequest;
        if (!current()) return;
        drawEvidence(OV.describeEvidence(context.body, state.history));

        const url = detailUrl(context.body);
        if (!url) {
            const why = { message: context.body ? (context.body.nextActions || [])[0] || "No workflow applies to the application." : "The recommendation context could not be read." };
            state.risk = OV.describeRisk(null, why);
            drawRisk(state.risk);
            drawPlacement(null, why.message);
            showStrip();
            return;
        }
        const detail = await attempt(url, { timeout: 120000 });
        if (!current()) return;
        state.risk = OV.describeRisk(detail.body || null, detail.error ? { message: detail.error.message } : null);
        drawRisk(state.risk);
        drawPlacement(detail.body || null, detail.error ? detail.error.message : null);
        showStrip();
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
            loadResearch();
        } catch (error) {
            applyBadge(badge, { variant: "danger", text: "Not showing live data" });
            if (OV) drawStrip(OV.describeChips({ discovery: false }));
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
