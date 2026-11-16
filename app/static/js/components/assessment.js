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
        if (apps.length > 1 && !selected) select.append(el("option", "", "Choose an application"));
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
        const rows = body.nodes || [];
        const hurting = rows.filter((row) => row.servicesDown.length || (typeof row.blastRadius === "number" && row.blastRadius > 0));
        const workflow = body.risk.scope === "workflow";
        if (!hurting.length) return "No single worker failure takes a service offline.";
        const all = hurting.length === rows.length;
        const lead = all ? `Any one of your ${rows.length} workers` : `${hurting.length} of your ${rows.length} workers`;
        if (workflow) {
            const worst = typeof body.risk.blastRadius === "number" ? ` The worst one breaks ${percent(body.risk.blastRadius)} of the weighted workflows.` : "";
            return `${lead} can break a business workflow.${worst}`;
        }
        return `${lead} stopping takes a service offline.`;
    }

    function drawStats(body) {
        const stats = $("assess-stats");
        stats.replaceChildren();
        const exposed = (body.vulnerableServices || []).filter((row) => row.lostOn.length).length;
        const evaluated = (body.evidence.workflowsEvaluated || []).length;
        const items = [
            [String((body.nodes || []).length), "workers tested"],
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

    function hostedOn(placement, node) {
        const hosted = [];
        for (const row of placement) {
            const here = (row.nodes || []).find((entry) => entry.node === node);
            if (here) hosted.push({ service: row.service, pods: here.readyPods, total: row.readyReplicas });
        }
        return hosted;
    }

    function drawNodes(body) {
        const holder = $("assess-nodes");
        holder.replaceChildren();
        const placement = body.placement || [];
        const rows = body.nodes || [];
        if (!rows.length) {
            holder.append(el("p", "assess-hint", "No worker node was assessed."));
            return;
        }
        const ordered = [...rows].sort((a, b) => a.node.localeCompare(b.node));
        const picked = rows.find((row) => row.node === selectedNode) || rows[0];
        selectedNode = picked.node;
        for (const row of ordered) {
            const failing = row.node === selectedNode;
            const column = el("button", "assess-node", undefined);
            column.type = "button";
            column.setAttribute("aria-pressed", failing ? "true" : "false");
            column.dataset.failing = failing ? "true" : "false";
            const head = el("span", "assess-node__head");
            head.append(el("strong", "", row.node), el("span", "assess-node__state", failing ? "Stopped (simulated)" : "Running"));
            column.append(head);
            const list = el("span", "assess-node__list");
            for (const item of hostedOn(placement, row.node)) {
                let state = "ok";
                if (failing) state = row.servicesDown.includes(item.service) ? "bad" : row.servicesDegraded.includes(item.service) ? "warn" : "ok";
                const chip = el("span", "assess-chip");
                chip.dataset.state = state;
                chip.append(el("span", "assess-chip__name", item.service));
                const replicas = item.total > 1 ? `${item.pods} of ${item.total}` : "1 replica";
                chip.append(el("span", "assess-chip__meta", failing && state === "bad" ? "offline" : replicas));
                list.append(chip);
            }
            if (!list.childNodes.length) list.append(el("span", "assess-hint", "Nothing scheduled here"));
            column.append(list);
            column.addEventListener("click", () => {
                selectedNode = row.node;
                drawNodes(body);
                drawConsequence(body);
            });
            holder.append(column);
        }
    }

    function drawConsequence(body) {
        const row = (body.nodes || []).find((item) => item.node === selectedNode);
        const line = $("assess-consequence");
        if (!row) { line.textContent = ""; return; }
        const parts = [];
        if (row.servicesDown.length) parts.push(`${row.servicesDown.join(", ")} would go offline`);
        for (const name of row.servicesDegraded) {
            const place = (body.placement || []).find((item) => item.service === name);
            parts.push(place && place.readyReplicas ? `${name} would keep ${place.readyReplicas - ((place.nodes.find((n) => n.node === row.node) || {}).readyPods || 0)} of ${place.readyReplicas} replicas` : `${name} would lose replicas`);
        }
        let text = parts.length ? `If ${row.node} stops, ${parts.join("; ")}.` : `If ${row.node} stops, every service keeps running.`;
        if (typeof row.blastRadius === "number") text += ` Weighted workflow loss: ${percent(row.blastRadius)}.`;
        else if (body.risk.scope !== "workflow") text += " Workflow impact is not scored without a reviewed workflow.";
        line.textContent = text;
    }

    /* ---------- fix ---------- */

    function meter(metric) {
        const box = el("div", "assess-meter");
        box.append(el("p", "assess-meter__label", metric.label));
        for (const [name, value, kind] of [["Now", metric.before, "before"], ["After", metric.after, "after"]]) {
            const rowEl = el("div", "assess-meter__row");
            const track = el("div", "assess-meter__track");
            const fill = el("span", "assess-meter__fill");
            fill.dataset.kind = kind;
            fill.style.width = `${Math.max(2, Math.min(100, value))}%`;
            track.append(fill);
            rowEl.append(el("span", "assess-meter__name", name), track, el("strong", "assess-meter__value", percent(value)));
            box.append(rowEl);
        }
        return box;
    }

    /* The sidebar already knows how pages are addressed (/components/x on the web, x.html in the Docker extension). */
    function pageHref(key, fallback) {
        const link = Array.from(document.querySelectorAll(".sidebar a[href]")).find((item) => item.getAttribute("href").includes(key));
        return link ? link.getAttribute("href") : fallback;
    }

    function handoff(body, label, primary) {
        const next = body.continuation;
        const link = el("a", primary ? "primary-button" : "secondary-button", label);
        link.href = pageHref("recommendations", (next && next.href) || "/components/recommendations");
        link.addEventListener("click", () => {
            if (!next || !next.fingerprint || !next.failedNode) return;
            try {
                const record = { namespace: next.namespace || "", failedNode: next.failedNode, fingerprint: next.fingerprint, at: Date.now() };
                const candidate = next.candidate;
                if (candidate && candidate.service && candidate.type) {
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
        const best = recommendation.best;
        const tag = $("assess-fix-tag");
        const holder = $("assess-fix-body");
        tag.textContent = recommendation.mode === "deployment" ? "Predicted · service level" : "Predicted";
        holder.replaceChildren();
        if (!best) {
            holder.append(el("p", "assess-fix__why", recommendation.note || "No feasible improvement was produced."));
            for (const item of recommendation.blocked || []) holder.append(el("p", "assess-hint", `${item.title}: ${item.limitation}`));
            const actions = el("div", "assess-actions");
            actions.append(handoff(body, "Open recommendations", false));
            holder.append(actions);
            return;
        }
        const grid = el("div", "assess-fix__grid");
        const copy = el("div", "assess-fix__copy");
        copy.append(el("h4", "assess-fix__title", best.title));
        if (best.why) copy.append(el("p", "assess-fix__why", best.why));
        const costs = el("p", "assess-fix__cost");
        costs.append(el("span", "assess-pill", `Cost: ${best.cost ? best.cost.toLowerCase() : "unknown"}`), el("span", "assess-hint", best.costDetail || ""));
        copy.append(costs);
        grid.append(copy);
        if (best.metric) grid.append(meter(best.metric));
        holder.append(grid);

        const follow = recommendation.followUps || [];
        if (follow.length) {
            const box = el("div", "assess-follow");
            box.append(el("p", "assess-follow__title", `This does not protect everything. ${plural(follow.length, "other service")} still go${follow.length === 1 ? "es" : ""} offline on a different worker:`));
            const list = el("ul", "assess-follow__list");
            for (const item of follow) {
                const entry = el("li");
                entry.append(el("strong", "", item.service), el("span", "", ` if ${item.node} stops — ${item.title.replace(/ for .*$/, "").toLowerCase()}`));
                list.append(entry);
            }
            box.append(list);
            holder.append(box);
        }

        const alts = recommendation.alternatives || [];
        if (alts.length) holder.append(el("p", "assess-hint", `Also possible: ${alts.map((item) => item.title.replace(/ for .*$/, "").toLowerCase()).join("; ")}.`));

        const actions = el("div", "assess-actions");
        actions.append(handoff(body, (body.continuation && body.continuation.label) || "Open recommendations", true));
        if (recommendation.mode === "deployment") {
            const link = el("a", "secondary-button", "Add a workflow for business impact");
            link.href = pageHref("workflows", "/components/workflows");
            actions.append(link);
        }
        holder.append(actions);
        holder.append(el("p", "assess-hint", recommendation.mode === "deployment"
            ? "Nothing is changed. Without a reviewed workflow this stays a service-level prediction, so it cannot be approved here."
            : "Nothing is changed. Approval is a separate step that checks the change again."));
    }

    /* ---------- evidence ---------- */

    function drawEvidence(body) {
        const holder = $("assess-evidence-body");
        holder.replaceChildren();
        const evidence = body.evidence || {};
        const list = el("ul", "assess-evidence__list");
        const add = (text) => { if (text) list.append(el("li", "", text)); };
        add(body.capturedAt ? `Placement read from the cluster at ${new Date(body.capturedAt).toLocaleString()}; every worker was tested against that one snapshot.` : "Placement read from the cluster.");
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
        $("assess-verdict").dataset.level = risk.level || "unknown";
        $("assess-level").textContent = LEVEL[risk.level] || "Unknown";
        $("assess-kicker").textContent = risk.scope === "workflow" ? "Workflow impact" : "Service-level view";
        $("assess-headline").textContent = heroTitle(body);
        $("assess-scope").textContent = risk.scope === "workflow"
            ? `${body.namespace} · scored against ${plural((body.evidence.workflowsEvaluated || []).length, "reviewed workflow")}`
            : `${body.namespace} · no reviewed workflow yet, so business impact is not guessed`;
        drawStats(body);
        drawNodes(body);
        drawConsequence(body);
        drawFix(body);
        drawEvidence(body);
    }

    async function fetchAssessment(url) {
        let lastError;
        for (let attempt = 0; attempt < 4; attempt += 1) {
            try {
                return await fetchJson(url, { timeout: 120000 });
            } catch (error) {
                lastError = error;
                const transient = RETRYABLE.has(error && error.status) || !(error && error.status);
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
            showState("Assessment unavailable", "The assessment could not be read.");
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
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => load());
    else load();
})();
