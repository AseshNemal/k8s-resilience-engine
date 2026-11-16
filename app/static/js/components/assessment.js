/*
 * Assessment page. Draws GET /api/assessment. Read-only: the only write is a one-shot handoff
 * the review page already accepts. Text is assigned with textContent.
 */
(function () {
    "use strict";

    const { fetchJson } = window.ResilienceApi;
    const { describeError, renderStateCard } = window.ResilienceConnection;
    const HANDOFF_KEY = "resilience.failure-impact.handoff.v1";
    const LEVEL = { critical: "Critical", high: "High", moderate: "Moderate", low: "Low", none: "Resilient", unknown: "Unknown" };

    const form = document.querySelector("[data-role='assess-form']");
    if (!form) return;
    const select = document.querySelector("[data-role='assess-application']");
    const run = document.querySelector("[data-role='assess-run']");
    const errorRegion = document.querySelector("[data-role='assess-error']");
    const empty = document.querySelector("[data-role='assess-empty']");
    const emptyTitle = document.querySelector("[data-role='assess-empty-title']");
    const emptyText = document.querySelector("[data-role='assess-empty-text']");
    const result = document.querySelector("[data-role='assess-result']");
    const verdict = document.querySelector("[data-role='assess-verdict']");
    const nodes = document.querySelector("[data-role='assess-nodes']");
    const services = document.querySelector("[data-role='assess-services']");
    const fixBody = document.querySelector("[data-role='assess-fix-body']");
    const fixTag = document.querySelector("[data-role='assess-fix-tag']");
    const evidenceBody = document.querySelector("[data-role='assess-evidence-body']");

    const el = (tag, className, text) => {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    };

    let request = 0;

    function showEmpty(title, text) {
        result.hidden = true;
        empty.hidden = false;
        emptyTitle.textContent = title;
        emptyText.textContent = text;
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
            const option = el("option", "", app.services && app.services.length ? `${app.namespace} · ${app.services.length} services` : app.namespace);
            option.value = app.namespace;
            if (app.namespace === selected) option.selected = true;
            select.append(option);
        }
        select.disabled = false;
        run.disabled = false;
    }

    function drawNodes(rows) {
        nodes.replaceChildren();
        if (!rows.length) {
            nodes.append(el("li", "assess-note", "No worker node was assessed."));
            return;
        }
        const max = Math.max(...rows.map((row) => Number(row.blastRadius) || row.servicesDown.length || 0), 1);
        for (const row of rows) {
            const item = el("li", "assess-node");
            const copy = el("div");
            copy.append(el("p", "assess-node__text", row.headline || row.node));
            const meta = [row.node];
            if (typeof row.blastRadius === "number") meta.push(`${row.blastRadius}% workflow loss`);
            else if (row.servicesDown.length) meta.push(`${row.servicesDown.length} service${row.servicesDown.length === 1 ? "" : "s"} lost`);
            else meta.push("No service lost");
            copy.append(el("p", "assess-node__meta", meta.join(" · ")));
            const width = Math.max(4, Math.round(((Number(row.blastRadius) || row.servicesDown.length || 0) / max) * 100));
            const bar = el("div", "assess-node__bar");
            const fill = el("span");
            fill.style.width = `${width}%`;
            bar.append(fill);
            copy.append(bar);
            item.append(el("span", "assess-node__rank", String(row.rank || "")), copy);
            nodes.append(item);
        }
    }

    function drawServices(rows) {
        services.replaceChildren();
        if (!rows.length) {
            services.append(el("p", "assess-note", "No service loses its last ready replica when one worker stops."));
            return;
        }
        for (const row of rows) {
            const block = el("div", "assess-service");
            const where = row.lostOn.length ? `Lost if ${row.lostOn.join(", ")} stops` : `Degraded if ${(row.degradedOn || []).join(", ")} stops`;
            const replicas = row.readyReplicas == null ? "" : ` · ${row.readyReplicas} ready`;
            block.append(el("strong", "", row.service), el("p", "", where + replicas));
            services.append(block);
        }
    }

    function fact(label, value) {
        const block = el("div");
        block.append(el("span", "", label), el("strong", "", value));
        return block;
    }

    function drawFix(body) {
        const recommendation = body.recommendation || {};
        const best = recommendation.best;
        fixTag.textContent = recommendation.mode === "deployment" ? "Deployment-level prediction" : "Predicted";
        fixBody.replaceChildren();
        if (!best) {
            fixBody.append(el("p", "assess-note", recommendation.note || "No feasible improvement was produced."));
            const blocked = recommendation.blocked || [];
            if (blocked.length) {
                const list = el("ul", "assess-alts");
                for (const item of blocked) list.append(el("li", "", `${item.title}: ${item.limitation}`));
                fixBody.append(list);
            }
            fixBody.append(continuationLink(body));
            return;
        }
        fixBody.append(el("h4", "assess-fix__title", best.title));
        if (best.why) fixBody.append(el("p", "assess-note", best.why));
        const facts = el("div", "assess-facts");
        facts.append(
            fact("Predicted benefit", best.benefit || "Not reported"),
            fact("Cost", best.costDetail || "Not reported"),
            fact("Limitation", best.limitation || recommendation.note || "None reported"),
        );
        fixBody.append(facts);
        if (recommendation.alternatives && recommendation.alternatives.length) {
            const list = el("ol", "assess-alts");
            for (const item of recommendation.alternatives) list.append(el("li", "", item.title));
            fixBody.append(el("p", "assess-note", "Other feasible changes"), list);
        }
        if (recommendation.note) fixBody.append(el("p", "assess-note", recommendation.note));
        fixBody.append(continuationLink(body));
    }

    function continuationLink(body) {
        const actions = el("div", "assess-actions");
        if (!body.continuation) return actions;
        const link = el("a", "primary-button", body.continuation.label || "Open recommendations");
        link.href = body.continuation.href || "/components/recommendations";
        link.addEventListener("click", () => {
            const next = body.continuation;
            if (!next.fingerprint || !next.failedNode) return;
            try {
                window.localStorage.setItem(HANDOFF_KEY, JSON.stringify({
                    namespace: next.namespace || "", failedNode: next.failedNode, fingerprint: next.fingerprint, at: Date.now(),
                }));
            } catch (_error) { /* Recommendations can still be opened without the handoff. */ }
        });
        actions.append(link, el("p", "", body.continuation.note || ""));
        return actions;
    }

    function drawEvidence(body) {
        const evidence = body.evidence || {};
        evidenceBody.replaceChildren();
        const evaluated = evidence.workflowsEvaluated || [];
        const names = evaluated.map((item) => item.name || item.workflowId).filter(Boolean);
        evidenceBody.append(el("p", "", evidence.workflowText || "Workflow evidence was not reported."));
        if (names.length) evidenceBody.append(el("p", "", `Evaluated workflows: ${names.join(", ")}.`));
        const broken = evidence.workflowsAlreadyBroken || [];
        if (broken.length) evidenceBody.append(el("p", "", `${broken.length} workflow${broken.length === 1 ? " is" : "s are"} already not working, so that is not blamed on a node.`));
        const skipped = evidence.workflowsNotEvaluated || [];
        if (skipped.length) evidenceBody.append(el("p", "", `${skipped.length} workflow${skipped.length === 1 ? " was" : "s were"} left out because placement did not cover it.`));
        evidenceBody.append(el("p", "", evidence.prediction || ""));
        if (body.capturedAt) evidenceBody.append(el("p", "", `Topology captured ${body.capturedAt}.`));
    }

    function draw(body) {
        errorRegion.replaceChildren();
        fillApplications(body, body.namespace);
        if (body.state === "choose-application") {
            showEmpty("Choose an application", "This cluster has more than one application. Pick one, then assess it.");
            return;
        }
        if (body.state === "no-application") {
            showEmpty("No application found", "The connected cluster has no namespace with a running service.");
            return;
        }
        if (body.state !== "ready" || !body.risk) {
            showEmpty("Assessment unavailable", "The cluster did not return an assessment.");
            return;
        }
        empty.hidden = true;
        result.hidden = false;
        const risk = body.risk;
        verdict.dataset.level = risk.level || "unknown";
        document.querySelector("[data-role='assess-kicker']").textContent = risk.scope === "workflow" ? "Predicted workflow impact" : "Predicted service exposure";
        document.querySelector("[data-role='assess-headline']").textContent = risk.headline || "";
        document.querySelector("[data-role='assess-level']").textContent = LEVEL[risk.level] || "Unknown";
        const scope = [`Application ${body.namespace}`];
        if (typeof risk.blastRadius === "number") scope.push(`worst case ${risk.blastRadius}%`);
        else scope.push("workflow survival unknown");
        document.querySelector("[data-role='assess-scope']").textContent = scope.join(" · ");
        drawNodes(body.nodes || []);
        drawServices(body.vulnerableServices || []);
        drawFix(body);
        drawEvidence(body);
    }

    async function load(namespace) {
        const mine = ++request;
        run.disabled = true;
        errorRegion.replaceChildren();
        showEmpty("Assessing…", namespace ? `Reading ${namespace} and scoring every worker node.` : "Reading the connected cluster.");
        try {
            const url = namespace ? `/api/assessment?namespace=${encodeURIComponent(namespace)}` : "/api/assessment";
            const body = await fetchJson(url, { timeout: 120000 });
            if (mine !== request) return;
            draw(body);
        } catch (error) {
            if (mine !== request) return;
            showEmpty("Assessment unavailable", "The assessment could not be read.");
            renderStateCard(errorRegion, describeError(error), { onRetry: () => load(select.value || namespace) });
        } finally {
            if (mine === request) run.disabled = select.disabled;
        }
    }

    form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (select.value) load(select.value);
    });
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => load());
    else load();
})();
