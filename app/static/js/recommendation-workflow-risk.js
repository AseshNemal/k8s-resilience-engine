/*
 * Workflows at risk, for the Recommendations page. Owned by: Member 4.
 *
 * The workflows are what the application is for, so this is drawn first: one row per workflow (most critical first), one column per
 * worker node, and in each cell whether that workflow survives the loss of that worker. "Today" is the observed placement; "After the
 * selected fix" is the same grid for the candidate the person has selected. Every cell is Component 3's prediction, as the server
 * returned it (`workflowRisk`, and the candidate's `workflows.exposedAt`): nothing is recalculated here. A fix whose result is not
 * known is drawn as "Not known", never as "Survives". Every cell says its state in words, not only in colour. Text goes through
 * textContent.
 */
(function (global) {
    "use strict";

    const list = (value) => (Array.isArray(value) ? value : []);

    /**
     * The grid as data. `risk` is the server's per-workflow list, `nodes` the worker names, `candidate` the selected candidate (or null).
     * After-the-fix cells are "breaks" / "survives", or "unknown" when the candidate carries no prediction.
     */
    function buildModel(risk, nodes, candidate, failedNode) {
        const rows = list(risk);
        if (!rows.length) return null;
        const names = [...list(nodes)];
        for (const row of rows) for (const node of list(row.breaksAt)) if (!names.includes(node)) names.push(node);
        const exposedAt = candidate && candidate.workflows && candidate.workflows.exposedAt && typeof candidate.workflows.exposedAt === "object"
            ? candidate.workflows.exposedAt : null;
        const workflows = rows.map((row) => {
            const today = list(row.breaksAt);
            const later = exposedAt ? list(exposedAt[row.workflowId]) : null;
            return {
                id: row.workflowId, criticality: Number(row.criticality),
                today: Object.fromEntries(names.map((n) => [n, today.includes(n) ? "breaks" : "survives"])),
                after: Object.fromEntries(names.map((n) => [n, later === null ? "unknown" : later.includes(n) ? "breaks" : "survives"])),
                breaksToday: today, breaksAfter: later,
            };
        });
        const atRiskToday = workflows.filter((w) => w.breaksToday.length).length;
        const known = workflows.every((w) => w.breaksAfter !== null);
        const stillAtRisk = known ? workflows.filter((w) => w.breaksAfter.length).length : null;
        return { nodes: names, workflows, failedNode: failedNode || null, atRiskToday, stillAtRisk, total: workflows.length,
            candidateKnown: !!candidate, afterKnown: known };
    }

    function summaryText(model) {
        const today = model.atRiskToday
            ? `${model.atRiskToday} of ${model.total} workflow${model.total === 1 ? "" : "s"} would break if the wrong worker fails.`
            : `All ${model.total} workflow${model.total === 1 ? "" : "s"} survive the loss of any one worker.`;
        if (!model.candidateKnown) return `${today} Select a fix to see what it changes.`;
        if (!model.afterKnown) return `${today} The selected fix has no prediction, so its result is not known.`;
        if (!model.stillAtRisk) return `${today} The selected fix keeps every workflow alive after any one worker fails (predicted).`;
        const saved = model.atRiskToday - model.stillAtRisk;
        return `${today} After the selected fix, ${model.stillAtRisk} would still break${saved > 0 ? ` and ${saved} would be protected` : ""} (predicted).`;
    }

    function element(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, value);
        return node;
    }

    const WORDS = { breaks: "Breaks", survives: "Survives", unknown: "Not known" };
    const MARKS = { breaks: "✕", survives: "✓", unknown: "?" };

    function grid(doc, model, which, title) {
        const panel = element(doc, "div", undefined, "rec-wf__panel");
        panel.append(element(doc, "h4", title, "rec-wf__title"));
        const scroll = element(doc, "div", undefined, "rec-wf__scroll");
        const table = element(doc, "table", undefined, "rec-wf__grid");
        const head = element(doc, "tr");
        head.append(element(doc, "th", "Workflow", undefined, { scope: "col" }));
        for (const node of model.nodes) {
            const th = element(doc, "th", node, node === model.failedNode ? "rec-wf__assessed" : undefined, { scope: "col" });
            if (node === model.failedNode) th.setAttribute("title", "The worker this page is assessing");
            head.append(th);
        }
        table.append(head);
        for (const workflow of model.workflows) {
            const tr = element(doc, "tr");
            const label = element(doc, "th", undefined, "rec-wf__name", { scope: "row" });
            label.append(element(doc, "span", workflow.id));
            if (Number.isFinite(workflow.criticality)) {
                label.append(element(doc, "span", `criticality ${workflow.criticality}`, "rec-wf__crit",
                    { style: `--crit:${Math.max(0, Math.min(1, workflow.criticality)) * 100}%` }));
            }
            tr.append(label);
            for (const node of model.nodes) {
                const state = workflow[which][node];
                const cell = element(doc, "td", undefined, "rec-wf__cell", { "data-state": state,
                    title: `${workflow.id} ${state === "breaks" ? "breaks" : state === "survives" ? "survives" : "is not known"} if ${node} fails` });
                cell.append(element(doc, "span", MARKS[state], "rec-wf__mark", { "aria-hidden": "true" }), element(doc, "span", WORDS[state]));
                tr.append(cell);
            }
            table.append(tr);
        }
        scroll.append(table);
        panel.append(scroll);
        return panel;
    }

    /** Draws the grids into `container`; returns the model, or null (and an explanation) when there is nothing to show. */
    function render(container, risk, nodes, candidate, options) {
        const doc = (options && options.document) || global.document;
        container.replaceChildren();
        const model = buildModel(risk, nodes, candidate, options && options.failedNode);
        if (!model) return null;
        container.append(element(doc, "p", summaryText(model), "rec-wf__summary", { role: "status" }));
        const wrap = element(doc, "div", undefined, "rec-wf__grids");
        wrap.append(grid(doc, model, "today", "Today (observed placement)"));
        if (model.candidateKnown) wrap.append(grid(doc, model, "after", "After the selected fix (predicted)"));
        container.append(wrap);
        container.append(element(doc, "p", "Each cell is the model's prediction for losing that one worker. Nothing here was measured on the cluster.", "rec-wf__legend"));
        return model;
    }

    global.ResilienceWorkflowRisk = { buildModel, render, summaryText };
})(typeof window !== "undefined" ? window : globalThis);
