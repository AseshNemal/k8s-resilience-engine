/*
 * Assessment page. Draws GET /api/assessment. Read-only: the only write is a one-shot handoff
 * the review page already accepts. Text is assigned with textContent.
 */
(function () {
    "use strict";

    const { fetchJson } = window.ResilienceApi;
    const { describeError, renderStateCard } = window.ResilienceConnection;
    const HANDOFF_KEY = "resilience.failure-impact.handoff.v1";
    const LEVEL = { critical: "Critical", high: "High risk", moderate: "Moderate", low: "Low", none: "Resilient", unknown: "Unknown" };
    const RETRYABLE = new Set([502, 503, 504]);

    const $ = (role) => document.querySelector(`[data-role='${role}']`);
    const form = $("assess-form");
    if (!form) return;
    const select = $("assess-application");
    const run = $("assess-run");
    const errorRegion = $("assess-error");
    const empty = $("assess-empty");
    const result = $("assess-result");

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    };
    const plural = (count, one, many) => `${count} ${count === 1 ? one : many || `${one}s`}`;
    const percent = (value) => `${Math.round(value)}%`;

    let request = 0;
    let current = null;
    let selectedNode = null;
    let selectedFix = null;

    function showState(title, text, busy) {
        result.hidden = true;
        empty.hidden = false;
        empty.dataset.busy = busy ? "true" : "false";
        $("assess-empty-title").textContent = title;
        $("assess-empty-text").textContent = text;
    }

    function fillApplications(body, selected) {
        const apps = Array.isArray(body.applications) && body.applications.length
            ? body.applications
            : (body.namespaces || []).map((namespace) => ({ namespace, services: [] }));
        select.replaceChildren();
        if (!apps.length) {
            select.append(el("option", "", "No application"));
            select.disabled = true;
            run.disabled = true;
            return;
        }
        if (apps.length > 1 && !selected) {
            const placeholder = el("option", "", "Choose an application");
            placeholder.value = "";
            placeholder.disabled = true;
            select.append(placeholder);
        }
        for (const app of apps) {
            const option = el("option", "", app.services && app.services.length ? `${app.namespace} · ${plural(app.services.length, "service")}` : app.namespace);
            option.value = app.namespace;
            if (app.namespace === selected) option.selected = true;
            select.append(option);
        }
        select.disabled = false;
        run.disabled = false;
    }

    /* ---------- hero ---------- */

    function heroTitle(body) {
        if (!body.nodes?.length) return "No worker node was assessed.";
        return body.risk.headline || "Inspect the predicted impact of each worker failure.";
    }

    function drawStats(body) {
        const stats = $("assess-stats");
        stats.replaceChildren();
        const exposed = (body.vulnerableServices || []).filter((row) => row.lostOn.length).length;
        const evaluated = (body.evidence.workflowsEvaluated || []).length;
        const items = [
            [String((body.nodes || []).length), "workers modeled"],
            [String(exposed), exposed === 1 ? "service exposed" : "services exposed"],
            evaluated ? [String(evaluated), evaluated === 1 ? "workflow scored" : "workflows scored"] : ["–", "no workflow to score"],
        ];
        for (const [value, label] of items) {
            const cell = el("div", "assess-stat");
            cell.append(el("dd", "assess-stat__value", value), el("dt", "assess-stat__label", label));
            stats.append(cell);
        }
    }

    /* ---------- map ---------- */

    const Placement = window.ResiliencePlacement;
    let view = "today";

    function afterPlacement(body) {
        const best = selectedFix;
        return best && Array.isArray(best.afterPlacement) ? best.afterPlacement : null;
    }

    function drawNodes(body) {
        const holder = $("assess-nodes");
        const rows = body.nodes || [];
        if (!rows.length) {
            holder.replaceChildren(el("p", "assess-hint", "No worker node was assessed."));
            return;
        }
        if (!rows.some((row) => row.node === selectedNode)) selectedNode = rows[0].node;
        const after = afterPlacement(body);
        if (!after) view = "today";
        const switcher = $("assess-view");
        switcher.hidden = !after;
        switcher.querySelectorAll("button").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.view === view)));
        $("assess-map-hint").textContent = view === "after"
            ? "Simulated placement after the change. The scheduler decides where a new replica really lands."
            : "Observed placement. Pick a worker to see what stops if it fails; this is a prediction, nothing is stopped.";
        Placement.draw(holder, {
            nodes: rows, placement: body.placement || [], after, view, failedNode: selectedNode,
            onSelect(node) { selectedNode = node; drawNodes(body); drawConsequence(body); drawFix(body); },
        });
        const picked = rows.find((row) => row.node === selectedNode);
        Placement.drawWorkflows($("assess-workflows"), view === "today" ? picked && picked.workflows : null);
    }

    function describe(node, down, degraded, placement) {
        const parts = [];
        if (down.length) parts.push(`${down.join(", ")} would go offline`);
        for (const name of degraded) {
            const row = placement.find((item) => item.service === name);
            const outcome = row ? Placement.outcome(row, node) : null;
            parts.push(outcome ? `${name} would keep ${outcome.left} of ${outcome.of} replicas` : `${name} would lose replicas`);
        }
        return parts;
    }

    function drawConsequence(body) {
        const row = (body.nodes || []).find((item) => item.node === selectedNode);
        const line = $("assess-consequence");
        if (!row) { line.textContent = ""; return; }
        const after = view === "after" ? afterPlacement(body) : null;
        const placement = after || body.placement || [];
        const effect = after ? Placement.impact(after, row.node) : { down: row.servicesDown, degraded: row.servicesDegraded };
        const parts = describe(row.node, effect.down, effect.degraded, placement);
        const lead = after ? `After the change (simulated), if ${row.node} stops` : `If ${row.node} stops`;
        let text = parts.length ? `${lead}, ${parts.join("; ")}.` : `${lead}, no additional assessed service loses a ready replica.`;
        if (!after && typeof row.blastRadius === "number") text += ` Weighted workflow loss: ${percent(row.blastRadius)}.`;
        else if (!after && body.risk.scope !== "workflow") text += " Workflow impact is unknown for this assessment.";
        line.textContent = text;
    }

    /* ---------- fix ---------- */

    /* The sidebar already knows how pages are addressed (/components/x on the web, x.html in the Docker extension). */
    function pageHref(key, fallback) {
        const link = Array.from(document.querySelectorAll(".sidebar a[href]")).find((item) => item.getAttribute("href").includes(key));
        return link ? link.getAttribute("href") : fallback;
    }

    function handoff(body, label, primary) {
        const next = body.continuation;
        const link = el("a", primary ? "primary-button" : "secondary-button", label);
        link.href = pageHref("recommendations", (next && next.href) || "/components/recommendations");
        if (body.namespace) link.href += `?namespace=${encodeURIComponent(body.namespace)}`;
        link.addEventListener("click", () => {
            if (!next || !next.fingerprint || !next.failedNode) return;
            try {
                const record = { namespace: next.namespace || "", failedNode: next.failedNode, fingerprint: next.fingerprint, at: Date.now() };
                const candidate = selectedFix;
                if (candidate && candidate.service && candidate.type) {
                    record.id = candidate.id;
                    record.service = candidate.service;
                    record.type = candidate.type;
                }
                window.localStorage.setItem(HANDOFF_KEY, JSON.stringify(record));
            } catch (_error) { /* Recommendations can still be opened without the handoff. */ }
        });
        return link;
    }

    function drawFix(body) {
        const recommendation = body.recommendation || {};
        const holder = $("assess-fix-body");
        holder.replaceChildren();
        $("assess-fix-tag").textContent = recommendation.mode === "workflow" ? "Workflow prediction" : "Service prediction";
        const options = recommendation.options || [];
        if (!options.length) {
            holder.append(el("p", "assess-option__effect", "No feasible recommendation is available."),
                el("p", "assess-hint", recommendation.note || "Refresh the assessment to check the evidence."),
                handoff(body, "Inspect recommendation evidence", false));
            return;
        }
        holder.append(el("p", "assess-hint", `Evaluated for the loss of ${recommendation.failedNode || "the assessed worker"}. Outlined pods show simulated placement.`));
        const list = el("ol", "assess-options");
        for (const option of options) {
            const active = selectedFix?.id === option.id;
            const item = el("li", window.ResilienceVisual ? "assess-option assess-option--visual" : "assess-option");
            item.dataset.active = String(active);
            const heading = el("div", "assess-option__head");
            const rank = option.rank == null ? "Unranked" : `#${option.rank}`;
            heading.append(el("strong", "assess-option__rank", rank), el("h4", "", option.title));
            if (option.id === recommendation.best?.id) heading.append(el("span", "assess-pill", "Engine choice"));
            const Visual = window.ResilienceVisual;
            if (Visual) {
                const workers = [...new Set([...(body.nodes || []).map((row) => row.node), ...((option.afterPlacement || []).flatMap((row) => row.nodes.map((entry) => entry.node)))])].sort();
                item.append(heading, Visual.strategyHead(document, option), Visual.changeCard(document, option, body.placement, workers, selectedNode));
            } else item.append(heading, Placement.changeFigure(option, body.placement, option.afterPlacement));
            if (option.metric) {
                item.append(el("p", "assess-option__effect", `${option.metric.label} · ${percent(option.metric.before)} → ${percent(option.metric.after)}`));
            } else if (option.benefit) item.append(el("p", "assess-option__effect", option.benefit));
            item.append(el("p", "assess-hint", `Feasible in simulation · ${option.cost || "Unreported"} resource cost`));
            const showReason = option.id === recommendation.best?.id && option.why;
            if (showReason) item.append(el("p", "assess-hint", option.why));
            if (option.costDetail || (!showReason && option.why)) {
                const more = el("details", "assess-reason");
                more.append(el("summary", "", showReason ? "Rollout details" : "Reason and rollout cost"));
                if (!showReason && option.why) more.append(el("p", "", option.why));
                if (option.costDetail) more.append(el("p", "", option.costDetail));
                for (const part of option.parts || []) {
                    if (option.type === "COMBINATION") more.append(el("p", "", `${part.service} · ${part.type.replaceAll("_", " ").toLowerCase()}`));
                }
                item.append(more);
            }
            const actions = el("div", "assess-actions");
            const pick = el("button", "secondary-button", active ? "Preview selected" : "Preview change");
            pick.type = "button";
            pick.setAttribute("aria-pressed", String(active));
            pick.addEventListener("click", () => {
                selectedFix = option;
                view = option.afterPlacement ? "after" : "today";
                drawNodes(body);
                drawConsequence(body);
                drawFix(body);
                holder.querySelector('[aria-pressed="true"]')?.focus();
            });
            actions.append(pick);
            if (active) {
                actions.append(handoff(body, recommendation.mode === "workflow" ? "Review YAML and approval" : "Review service recommendation", true));
                if (recommendation.mode === "deployment") {
                    const link = el("a", "secondary-button", "Review workflow evidence");
                    link.href = pageHref("workflows", "/components/workflows") + `?namespace=${encodeURIComponent(body.namespace)}&purpose=recommendations`;
                    actions.append(link);
                }
            }
            item.append(actions);
            list.append(item);
        }
        holder.append(list);
        if (recommendation.blocked?.length) {
            const reasons = el("details", "assess-reason");
            reasons.append(el("summary", "", "Why other changes were ruled out"));
            for (const entry of recommendation.blocked) reasons.append(el("p", "", `${entry.title}. ${entry.limitation}`));
            holder.append(reasons);
        }
        if (recommendation.mode === "deployment") holder.append(el("p", "assess-hint", "Approval requires a reviewed workflow. Service predictions alone cannot establish business impact."));

    }

    /* ---------- evidence ---------- */

    function drawEvidence(body) {
        const holder = $("assess-evidence-body");
        holder.replaceChildren();
        const evidence = body.evidence || {};
        const list = el("ul", "assess-evidence__list");
        const add = (text) => { if (text) list.append(el("li", "", text)); };
        add(body.capturedAt ? `Placement read from the cluster at ${new Date(body.capturedAt).toLocaleString()}; each worker failure was modeled from this snapshot.` : "Placement read from the cluster.");
        add(evidence.workflowText);
        const names = (evidence.workflowsEvaluated || []).map((item) => item.name || item.workflowId).filter(Boolean);
        if (names.length) add(`Workflows scored: ${names.join(", ")}.`);
        const broken = evidence.workflowsAlreadyBroken || [];
        if (broken.length) add(`${plural(broken.length, "workflow")} already not working today, so it is not blamed on a node.`);
        const skipped = evidence.workflowsNotEvaluated || [];
        if (skipped.length) add(`${plural(skipped.length, "workflow")} left out because placement did not cover ${skipped.length === 1 ? "it" : "them"}.`);
        add(evidence.prediction);
        holder.append(list);
    }

    /* ---------- page ---------- */

    function draw(body) {
        errorRegion.replaceChildren();
        fillApplications(body, body.namespace);
        if (body.state === "choose-application") return showState("Choose an application", "This cluster has more than one application. Pick one above.");
        if (body.state === "no-application") return showState("No application found", "The connected cluster has no namespace with a running service.");
        if (body.state !== "ready" || !body.risk) return showState("Assessment unavailable", "The cluster did not return an assessment.");
        current = body;
        empty.hidden = true;
        result.hidden = false;
        const risk = body.risk;
        selectedNode = risk.worstNode || null;
        selectedFix = body.recommendation?.best || body.recommendation?.options?.[0] || null;
        view = "today";
        $("assess-verdict").dataset.level = risk.level || "unknown";
        $("assess-level").textContent = LEVEL[risk.level] || "Unknown";
        $("assess-kicker").textContent = risk.scope === "workflow" ? "Workflow impact" : "Service-level view";
        $("assess-headline").textContent = heroTitle(body);
        $("assess-scope").textContent = risk.scope === "workflow"
            ? `${body.namespace} · scored against ${plural((body.evidence.workflowsEvaluated || []).length, "reviewed workflow")}`
            : `${body.namespace} · business impact is unknown with the available workflow evidence`;
        if (body.capturedAt) {
            const captured = new Date(body.capturedAt);
            if (Number.isFinite(captured.getTime())) $("assess-scope").textContent += ` · Cluster read ${captured.toLocaleTimeString()}`;
        }
        const options = body.recommendation?.options || [];
        $("assess-compare").textContent = options.length ? `Compare ${plural(options.length, "feasible change")}` : "Inspect recommendation evidence";
        drawStats(body);
        drawNodes(body);
        drawConsequence(body);
        drawFix(body);
        drawEvidence(body);
    }

    $("assess-view").addEventListener("click", (event) => {
        const button = event.target.closest("button[data-view]");
        if (!button || !current) return;
        view = button.dataset.view;
        drawNodes(current);
        drawConsequence(current);
    });

    async function fetchAssessment(url) {
        let lastError;
        for (let attempt = 0; attempt < 4; attempt += 1) {
            try {
                return await fetchJson(url, { timeout: 120000 });
            } catch (error) {
                lastError = error;
                // A coded refusal (no cluster, credential rejected, nothing to assess) will not change in a second: answer at once.
                const transient = !(error && error.code) && (RETRYABLE.has(error && error.status) || !(error && error.status));
                if (!transient) throw error;
                await new Promise((resolve) => setTimeout(resolve, 700 * (attempt + 1)));
            }
        }
        throw lastError;
    }

    async function load(namespace) {
        const mine = ++request;
        run.disabled = true;
        errorRegion.replaceChildren();
        showState("Assessing…", namespace ? `Reading ${namespace} and testing every worker node.` : "Reading the connected cluster.", true);
        try {
            const body = await fetchAssessment(namespace ? `/api/assessment?namespace=${encodeURIComponent(namespace)}` : "/api/assessment");
            if (mine !== request) return;
            draw(body);
        } catch (error) {
            if (mine !== request) return;
            // One message, not two: the state panel replaces the "Assessing…" card instead of sitting beneath it.
            empty.hidden = true;
            result.hidden = true;
            select.replaceChildren(el("option", "", "No cluster"));
            select.disabled = true;
            renderStateCard(errorRegion, describeError(error), { onRetry: () => load(select.value || namespace) });
        } finally {
            if (mine === request) run.disabled = select.disabled;
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (select.value) load(select.value);
    });
    select.addEventListener("change", () => { if (select.value) load(select.value); });
    // When the cluster comes back (or is connected for the first time) and nothing is on screen yet, assess it without being asked.
    if (window.ResilienceCluster) window.ResilienceCluster.onChange((status) => { if (status.state === "connected" && !current) load(select.value || undefined); });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => load());
    else load();
})();
