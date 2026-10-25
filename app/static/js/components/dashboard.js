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

    function make(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text; // never parsed as HTML
        return node;
    }

    function clearFigures() {
        const headline = document.querySelector('[data-role="discovery-headline"]');
        if (headline) headline.textContent = "Reading cluster inventory…";
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

    function serviceTable(services, graph, namespace) {
        const table = make("table", "ns-table");
        const head = make("tr");
        for (const title of ["Service", "Ready / declared", "Runs on", "Placement exposure"]) {
            const cell = make("th", "", title); cell.setAttribute("scope", "col"); head.append(cell);
        }
        const thead = make("thead");
        thead.append(head);
        table.append(thead);
        const body = make("tbody");
        for (const service of services) {
            const row = make("tr");
            row.setAttribute("data-service", service.name);
            row.setAttribute("data-namespace", namespace);
            const observed = graph && (graph.services || []).find((s) => s.name === service.name);
            const info = OV && OV.describeService(observed);
            const replicas = make("td");
            replicas.append(make("span", "overview-replica-count", info ? info.replicas : service.replicas));
            if (info && info.readyShare !== null) replicas.append(meter(info.readyShare, info.readiness, info.replicas));
            const exposure = make("td");
            if (info) {
                exposure.append(statePill(info.concentrated === true ? "incomplete" : "neutral", info.concentrated === true ? "Single node" : info.concentrated === false ? "Distributed" : "Unknown"));
                if (info.concentration !== null) {
                    const label = `${Number((info.concentration * 100).toFixed(1))}% on busiest node`;
                    exposure.append(make("small", "overview-caption", label), meter(info.concentration, info.concentrated ? "incomplete" : "neutral", label));
                }
            } else exposure.append(make("span", "overview-caption", "Unavailable"));
            row.append(make("td", "", service.name), replicas, make("td", "", service.nodes.join(", ")), exposure);
            body.append(row);
        }
        table.append(body);
        return table;
    }

    function meter(value, state, label) {
        const track = make("div", "overview-mini-meter");
        track.setAttribute("data-state", state);
        track.setAttribute("role", "img");
        track.setAttribute("aria-label", label);
        const fill = make("span"); fill.style.width = `${Math.min(100, Math.max(0, value * 100))}%`;
        track.append(fill); return track;
    }

    function namespaceCard(row, graph) {
        const card = make("article", "ns-card");
        const head = make("div", "ns-card__summary");
        const state = make("span");
        applyBadge(state, row.badge);
        head.append(make("strong", "ns-card__name", row.namespace), state,
            make("span", "ns-card__counts", `${row.services.length} services · ${row.unresolved} unresolved`));
        card.append(head);
        if (row.services.length) {
            const inventory = make("div", "ns-card__inventory");
            inventory.append(serviceTable(row.services, graph, row.namespace));
            card.append(inventory);
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
        scope: document.querySelector('[data-role="overview-scope"]'),
        next: document.querySelector('[data-role="overview-next-text"]'),
        nextLink: document.querySelector('[data-role="overview-next-link"]'),
        nodeImpact: document.querySelector('[data-role="overview-node-impact"]'),
        workflows: document.querySelector('[data-role="overview-workflow-impact"]'),
        workflowCaption: document.querySelector('[data-role="overview-workflow-caption"]'),
        fix: document.querySelector('[data-role="overview-fix"]'),
        latest: document.querySelector('[data-role="overview-latest-test"]'),
        focusSummary: document.querySelector('[data-role="overview-focus-summary"]'),
        focusClear: document.querySelector('[data-role="overview-focus-clear"]'),
        focusView: document.querySelector('[data-role="overview-focus-view"]'),
        placementFocus: document.querySelector('[data-role="overview-placement-focus"]'),
    };
    const STATE_WORD = { available: "Ready", missing: "Needed", incomplete: "Partial", pending: "Checking" };
    let researchRun = 0;
    let focusDetail = null;
    let focused = null;
    let focusButtons = [];

    function applyHighlight() {
        sections.placement.setAttribute("data-focus-active", String(!!focused));
        for (const item of sections.placement.querySelectorAll("[data-service], [data-node]")) {
            const service = item.getAttribute("data-service"), node = item.getAttribute("data-node");
            const matches = !!focused && (!service || focused.services.includes(service)) && (!node || node === focused.node);
            item.setAttribute("data-highlighted", String(matches));
        }
        for (const item of namespacesRegion.querySelectorAll("[data-service]")) {
            const matches = !!focused && item.getAttribute("data-namespace") === focused.namespace && focused.services.includes(item.getAttribute("data-service"));
            item.setAttribute("data-highlighted", String(matches));
        }
        for (const record of focusButtons) record.button.setAttribute("aria-pressed", String(!!focused && record.node === focused.node && (!record.workflow || record.workflow === focused.workflow)));
    }

    function showFocus() {
        sections.focusSummary.textContent = focused ? focused.message : "Select a node or workflow cell to highlight its placement.";
        sections.placementFocus.textContent = focused ? `Highlighted: ${focused.workflow ? `${focused.workflow} · ` : ""}${focused.node}. The assessed failure remains unchanged.` : "";
        sections.placementFocus.hidden = !focused;
        sections.focusClear.hidden = !focused;
        sections.focusView.hidden = !focused;
        applyHighlight();
    }

    function selectFocus(workflow, node) {
        focused = focused && focused.workflow === workflow && focused.node === node ? null : OV.describeFocus(focusDetail, workflow, node);
        showFocus();
    }

    function focusButton(button, node, workflow) {
        button.type = "button";
        button.setAttribute("aria-pressed", "false");
        button.addEventListener("click", () => selectFocus(workflow, node));
        focusButtons.push({ button, node, workflow });
        return button;
    }

    function drawNext(detail, reason) {
        const action = OV.describeAction(detail, reason);
        sections.next.replaceChildren(make("p", "overview-action-title", action.title));
        for (const change of action.changes) {
            const row = make("p", "overview-action-change");
            row.append(make("strong", "", change.service), make("span", "", change.change));
            sections.next.append(row);
        }
        if (action.reviewable) {
            const metrics = make("div", "overview-action-metrics");
            const benefit = make("div");
            benefit.append(make("span", "overview-caption", "Predicted reduction"), make("strong", "", action.benefit === null ? "Not reported" : `${action.benefit} pp`));
            const cost = make("div");
            cost.append(make("span", "overview-caption", "Resource cost"), make("strong", "", action.cost));
            if (action.costNote) cost.append(help("Resource cost", action.costNote));
            metrics.append(benefit, cost); sections.next.append(metrics);
            if (action.explanation) sections.next.append(help("Proposed change", action.explanation));
        } else sections.next.append(make("p", "overview-caption", action.explanation));
        sections.nextLink.textContent = action.reviewable ? "Review proposed change" : "Review evidence";
    }

    let helpId = 0;
    function help(label, detail) {
        const control = make("span", "overview-help", "ⓘ");
        const id = `overview-help-${++helpId}`;
        control.setAttribute("tabindex", "0");
        control.setAttribute("aria-label", label);
        control.setAttribute("aria-describedby", id);
        const tip = make("span", "overview-tooltip", detail);
        tip.id = id; tip.setAttribute("role", "tooltip");
        control.append(tip); return control;
    }

    // The links are in the page itself (so the extension build can point them at its own files); this fills in each one's state and detail.
    function drawStrip(chips) {
        for (const chip of chips) {
            const link = document.querySelector(`[data-role="overview-chip-${chip.id}"]`);
            const state = document.querySelector(`[data-role="overview-chip-${chip.id}-state"]`);
            const detail = document.querySelector(`[data-role="overview-chip-${chip.id}-detail"]`);
            if (link) link.setAttribute("data-state", chip.state);
            if (state) state.textContent = chip.statusLabel || STATE_WORD[chip.state] || chip.state;
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
        headers.forEach((title) => { const cell = make("th", "", title); cell.setAttribute("scope", "col"); head.append(cell); });
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

    function drawResults(rows, history) {
        if (!sections.results) return;
        if (history === null) {
            sections.results.replaceChildren(make("p", "ui-note", "Saved tests could not be read. Refresh to retry.")); return;
        }
        if (!rows.length) {
            sections.results.replaceChildren(make("p", "ui-note", "No fix has been applied and tested yet. Apply one on the Recommendations page."));
            return;
        }
        sections.results.replaceChildren(table(["Fix", "Application / cluster", "Before", "Proposed", "Recalculated", "Test outcome", "Date (UTC)"],
            rows.map((row) => [row.name, `${row.namespace} / ${row.cluster}`, row.baseline, row.predicted, row.after, statePill(row.outcomeState, row.outcome), row.when])));
    }

    function bar(label, value, state) {
        const row = make("div", "overview-bar-row");
        row.setAttribute("data-state", state || (value > 0 ? "loss" : "safe"));
        const head = make("div", "overview-bar-row__head");
        head.append(make("span", "", label), make("strong", "", value === null ? "Unavailable" : `${value}%`));
        row.append(head);
        if (value !== null) {
            const track = make("div", "overview-bar-track");
            const fill = make("div", "overview-bar-fill");
            fill.style.width = `${Math.max(0, Math.min(100, value))}%`;
            track.setAttribute("aria-hidden", "true"); track.append(fill); row.append(track);
        }
        return row;
    }

    function drawImpact(detail, reason) {
        const model = OV.describeImpact(detail);
        focusButtons = [];
        sections.nodeImpact.replaceChildren(...(model.nodes.length ? model.nodes.map((r) => {
            const button = focusButton(make("button", "overview-node-button"), r.node, null);
            button.setAttribute("aria-label", `Highlight placement on ${r.node}`);
            button.append(bar(r.node, r.value)); return button;
        })
            : [make("p", "ui-note", reason || "Per-node impact was not reported.")]));
        sections.workflowCaption.textContent = "Select a cell to trace its placement.";
        sections.workflows.replaceChildren();
        if (!model.workflows.length || !model.nodes.length) {
            sections.workflows.append(make("p", "ui-note", reason || "Workflow exposure is unavailable.")); return;
        }
        const grid = table(["Workflow", ...model.nodes.map((r) => r.node)], model.workflows.map((w) => [
            w.name, ...model.nodes.map((r) => {
                const failed = w.failures === null ? null : w.failures.includes(r.node);
                const cell = focusButton(make("button", "overview-survival-cell", failed === null ? "? Unknown" : failed ? "× Fails" : "✓ Survives"), r.node, w.name);
                cell.setAttribute("data-state", failed === null ? "unknown" : failed ? "failed" : "passed");
                cell.setAttribute("aria-label", `${w.name}: ${failed === null ? "unknown" : failed ? "fails" : "survives"} if ${r.node} fails. Highlight placement`);
                return cell;
            }),
        ]));
        sections.workflows.append(grid);
        applyHighlight();
    }

    function drawFix(detail, reason) {
        const selected = detail && (detail.candidates || []).find((c) => c.selected);
        sections.fix.replaceChildren();
        if (!selected) {
            sections.fix.append(make("p", "ui-note", reason || detail?.outcome?.message || "No candidate is selected.")); return;
        }
        sections.fix.append(make("p", "overview-fix-name", `${selected.type.replace(/_/g, " ").toLowerCase()} · ${selected.service}`));
        const predicted = selected.predicted || {};
        const flow = make("div", "overview-fix-flow");
        const value = (label, number) => {
            const box = make("div", "overview-fix-value");
            box.append(make("span", "overview-caption", label), make("strong", "", Number.isFinite(number) ? `${number}%` : "Unavailable"));
            return box;
        };
        flow.append(value("Current model", predicted.blastRadiusBefore), make("span", "overview-fix-arrow", "→"), value("Proposed model", predicted.blastRadiusAfter));
        sections.fix.append(flow);
        if (Number.isFinite(predicted.riskReduction)) sections.fix.append(make("p", "overview-fix-reduction", `${predicted.riskReduction} percentage points predicted reduction`));
        sections.fix.append(help("About the comparison", "Both values use the same assessed node and workflow set. The proposed value comes from the engine's simulation; it is not an experimental measurement."));
    }

    function drawLatest(history) {
        const run = history && Array.isArray(history.runs) && history.runs[0];
        sections.latest.replaceChildren();
        if (!run) {
            sections.latest.append(make("p", "ui-note", history === null ? "Saved tests could not be read. Refresh to retry." : "No saved test yet.")); return;
        }
        const row = OV.describeResults({ runs: [run] })[0];
        sections.latest.append(statePill(row.outcomeState, row.outcome), make("p", "overview-fix-name", row.name),
            make("p", "overview-caption", `${row.namespace} · ${row.cluster} · ${row.when} UTC`));
        const checks = run.afterWorkflows && run.afterWorkflows.rows;
        const evidence = make("div", "overview-test-evidence");
        const marker = (label, value, state) => {
            const item = make("div", "overview-test-evidence__item");
            item.setAttribute("data-state", state);
            item.append(make("span", "overview-test-evidence__dot", state === "available" ? "✓" : "—"), make("span", "", label), make("strong", "", value));
            evidence.append(item);
        };
        marker("After-rollout model", Number.isFinite(run.afterRisk) ? `${run.afterRisk}%` : "Unavailable", Number.isFinite(run.afterRisk) ? "available" : "missing");
        const passed = Array.isArray(checks) ? checks.filter((w) => w.httpStatus === "PASSED").length : null;
        marker("Workflow HTTP checks", passed === null || !checks.length ? "Not collected" : `${passed}/${checks.length} passed`, passed !== null && checks.length > 0 && passed === checks.length ? "available" : "missing");
        marker("Resource usage", run.afterResources === true ? "Measured" : "Unavailable", run.afterResources === true ? "available" : "missing");
        sections.latest.append(evidence);
        sections.latest.append(help("Recorded workflow checks", Array.isArray(checks) && checks.length
            ? checks.map((w) => `${w.workflowId}: ${w.httpStatus || "Unavailable"}`).join("; ") : "Post-apply workflow checks were not collected."),
            help("About this saved result", `Model risk: ${row.baseline} before → ${row.predicted} proposed → ${row.after} after rollout. Recalculated risk is a model result, not experimental proof. This saved run may use a different proposal or cluster.`));
    }

    function drawEvidence(items) {
        if (!sections.evidence) return;
        sections.evidence.replaceChildren(table(["Evidence", "State", "Detail"],
            items.map((item) => [item.label, statePill(item.state, STATE_WORD[item.state] || item.state), item.detail])));
    }

    function drawPlacement(detail, reason) {
        if (!sections.placement) return;
        focusDetail = detail;
        const compare = window.ResilienceTopologyCompare;
        if (!detail || !compare) {
            sections.placement.replaceChildren(make("p", "ui-note", reason || "No assessed application topology is available."));
            return;
        }
        sections.placement.replaceChildren();
        compare.render(sections.placement, detail.currentDeployment, null,
            { failedNode: (detail.evidence && detail.evidence.failedNode) || null, observedOnly: true });
        applyHighlight();
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
            drawResults(OV.describeResults(state.history), state.history);
            drawLatest(state.history);
            showStrip();
        });
        const context = await contextRequest;
        if (!current()) return;
        state.context = context.body || { workflows: [], declaredWorkflows: [], evidence: [{ id: "workflow", detail: context.error ? context.error.message : "Not available" }] };
        sections.scope.textContent = context.body?.namespace ? `Assessed application: ${context.body.namespace}. Cluster inventory covers all application namespaces.` : "Application scope unavailable.";
        drawNext(null, (context.body?.nextActions || [])[0]);
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
            drawImpact(null, why.message);
            drawFix(null, why.message);
            drawNext(null, why.message);
            showStrip();
            return;
        }
        const detail = await attempt(url, { timeout: 120000 });
        if (!current()) return;
        state.risk = OV.describeRisk(detail.body || null, detail.error ? { message: detail.error.message } : null);
        drawRisk(state.risk);
        drawPlacement(detail.body || null, detail.error ? detail.error.message : null);
        drawImpact(detail.body || null, detail.error ? detail.error.message : null);
        drawFix(detail.body || null, detail.error ? detail.error.message : null);
        drawNext(detail.body || null, detail.error ? detail.error.message : null);
        if (detail.body) {
            const body = detail.body;
            const basis = body.portfolio?.basis === "DECLARED" ? "Declared workflows" : "Frozen workflows";
            const captured = body.evidence?.topologyCapturedAt;
            const date = captured ? new Date(captured) : null;
            const captureLabel = date && !Number.isNaN(date.getTime())
                ? `${date.toLocaleString("en-GB", { timeZone: "UTC", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })} UTC` : "time unavailable";
            sections.scope.textContent = `${body.evidence?.namespace || context.body.namespace} · ${basis} · ${body.portfolio?.workflows?.length ?? "Unknown"} evaluated · captured ${captureLabel}`;
            if (body.portfolio?.notEvaluated?.length) sections.scope.textContent += ` · ${body.portfolio.notEvaluated.length} not evaluated`;
        }
        showStrip();
    }

    let overviewRun = 0;
    async function loadOverview() {
        const run = ++overviewRun;
        ++researchRun; // A late response from the earlier cluster must never redraw this refresh.
        if (errorRegion) errorRegion.replaceChildren();
        if (reloadButton) reloadButton.disabled = true;
        // Never leave the previous cluster's figures on screen while (or after) a refresh fails.
        clearFigures();
        if (OV) {
            focused = null;
            focusDetail = null;
            showFocus();
            drawApplied({ visible: false });
            drawStrip(OV.describeChips({}));
            drawRisk(OV.describeRisk(null, { pending: true }));
            drawImpact(null, "Reading the prediction…");
            drawFix(null, "Reading the recommendation…");
            drawPlacement(null, "Reading placement…");
            for (const region of [sections.results, sections.latest, sections.evidence]) region.replaceChildren(make("p", "ui-note", "Reading evidence…"));
            sections.scope.textContent = "Assessing application scope…";
            sections.next.replaceChildren(make("p", "overview-caption", "Reading the evidence…"));
        }
        applyBadge(badge, { variant: "muted", text: "Loading…" });

        try {
            const result = await fetchJson("/api/deployments/discovery/all");
            if (run !== overviewRun) return;
            const described = describeOverview(result);
            applyBadge(badge, described.badge);
            showSummary(described);
            if (namespacesRegion) namespacesRegion.replaceChildren(...described.rows.map((row) => namespaceCard(row, (result.namespaces || []).find((ns) => ns.namespace === row.namespace)?.graph)));
            updateLastRefreshed(lastRefreshed);
            loadResearch();
        } catch (error) {
            if (run !== overviewRun) return;
            applyBadge(badge, { variant: "danger", text: "Not showing live data" });
            const headline = document.querySelector('[data-role="discovery-headline"]');
            if (headline) headline.textContent = "Cluster inventory unavailable";
            if (OV) {
                drawStrip(OV.describeChips({ discovery: false, history: null }));
                drawRisk(OV.describeRisk(null, { message: error.message }));
                drawImpact(null, error.message); drawFix(null, error.message); drawPlacement(null, error.message);
                for (const region of [sections.results, sections.latest, sections.evidence]) region.replaceChildren(make("p", "ui-note", "Current cluster evidence is unavailable. Refresh to retry."));
                sections.scope.textContent = "Application scope unavailable.";
                drawNext(null, "Restore the cluster connection, then refresh the evidence.");
            }
            renderStateCard(errorRegion, describeError(error), { onRetry: loadOverview });
        } finally {
            if (run === overviewRun && reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadOverview);
    }
    if (sections.focusClear) sections.focusClear.addEventListener("click", () => { focused = null; showFocus(); });

    document.addEventListener("DOMContentLoaded", loadOverview);
})();
