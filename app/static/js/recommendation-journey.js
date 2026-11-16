/*
 * The Recommendations page as one journey: understand the risk, choose a fix, review it, approve, apply and test, keep or reset.
 * Owned by: Member 4 (L.A. Nemal).
 *
 * Display only. Every figure drawn here is one the engine returned (GET /api/recommendations/{live,declared}/detail): nothing is
 * scored, simulated or estimated in the browser, and a value the engine did not report is said to be unreported. A figure from the
 * what-if simulation is always labelled a prediction; "tested" is said only for a saved run the server reported.
 *
 *   stages(facts)                  where the work stands, as five stops and the one next action
 *   numbers(candidate)             the before / after figures to lead with (worst case across nodes when the engine reports it)
 *   renderRail / renderRisk / renderFixes / renderReview   the page's own sections
 *
 * Text from the API is always written with textContent, so it stays text.
 */
(function (global) {
    "use strict";

    const STRATEGY = {
        TOPOLOGY_SPREAD: "Spread replicas across nodes",
        REPLICA_INCREASE: "Add a replica",
        REPLICA_INCREASE_PLUS_2: "Add two replicas",
        REPLICA_INCREASE_ANTI_AFFINITY: "Add a replica, kept on another node",
        POD_ANTI_AFFINITY: "Keep replicas on different nodes",
        COMBINATION: "Combined fix",
    };

    const docOf = (options) => (options && options.document) || global.document;
    const workflowName = (id) => (global.ResilienceWorkflowNames ? global.ResilienceWorkflowNames.label(id) : id);
    const isNumber = (value) => typeof value === "number" && Number.isFinite(value);
    const percent = (value) => (isNumber(value) ? `${Number(value.toFixed(2))}%` : "not reported");
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

    function el(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
        return node;
    }

    /** "REPLICA_INCREASE" -> "Add a replica". A strategy this page has no words for is shown as the engine named it. */
    function strategyTitle(type) {
        if (STRATEGY[type]) return STRATEGY[type];
        const words = String(type || "").split("_").filter(Boolean).join(" ").toLowerCase();
        return words ? words.charAt(0).toUpperCase() + words.slice(1) : "Unnamed change";
    }

    /** What a fix is called: the strategy in plain words, and the services it touches. A combination of one strategy says so. */
    function fixName(candidate) {
        const services = String((candidate && candidate.service) || "").split("+").filter(Boolean);
        const parts = (candidate && Array.isArray(candidate.parts) && candidate.parts) || [];
        const kinds = [...new Set(parts.map((part) => part.type))];
        const title = candidate && candidate.type === "COMBINATION" && kinds.length === 1 ? `${strategyTitle(kinds[0])} to each` : strategyTitle(candidate && candidate.type);
        return { title, services, target: services.join(" + ") };
    }

    /**
     * The figures to lead with. When the engine reports the worst case across every node (the all-workflows evaluation does), that is
     * the headline, because a fix that only moves the worst case to another node has not removed it. The assessed node's own figures
     * are always kept beside it.
     */
    function numbers(candidate) {
        const predicted = (candidate && candidate.predicted) || {};
        const coverage = candidate && candidate.coverage;
        const node = { before: predicted.blastRadiusBefore, after: predicted.blastRadiusAfter };
        if (coverage && isNumber(coverage.worstCaseBefore) && isNumber(coverage.worstCaseAfter)) {
            return { scope: "worst", before: coverage.worstCaseBefore, after: coverage.worstCaseAfter, node };
        }
        return { scope: "node", before: node.before, after: node.after, node };
    }

    /** Does the engine predict any gain from this fix, on the assessed node or in the worst case? */
    function improves(candidate) {
        const n = numbers(candidate);
        const gain = (pair) => isNumber(pair.before) && isNumber(pair.after) && pair.after < pair.before;
        return gain(n) || gain(n.node);
    }

    /** Which workflows stop at which node, today and with a fix: read from the engine's exposure table, never worked out here. */
    function exposure(detail, candidate) {
        const table = (detail && detail.workflowExposure) || null;
        const risk = (detail && Array.isArray(detail.workflowRisk) && detail.workflowRisk) || [];
        const nodes = ((detail && detail.currentDeployment && detail.currentDeployment.nodes) || []).map((n) => n.name).filter(Boolean);
        const before = table && table.before ? table.before : Object.fromEntries(risk.filter((w) => Array.isArray(w.breaksAt)).map((w) => [w.workflowId, w.breaksAt]));
        const reportedAfter = candidate && table && table.after && table.after[candidate.id];
        const after = reportedAfter && Object.keys(before).every((id) => Array.isArray(reportedAfter[id])) ? reportedAfter : null;
        const criticality = new Map(risk.map((w) => [w.workflowId, w.criticality]));
        const workflows = Object.keys(before)
            .map((id) => ({ id, name: workflowName(id), criticality: criticality.has(id) ? criticality.get(id) : null,
                before: before[id] || [], after: after ? after[id] || [] : null }))
            .sort((a, b) => (b.criticality || 0) - (a.criticality || 0) || a.name.localeCompare(b.name));
        const safe = (list) => workflows.filter((w) => list(w) && list(w).length === 0).length;
        return { nodes, workflows, hasAfter: !!after,
            safeBefore: safe((w) => w.before), safeAfter: after ? safe((w) => w.after) : null };
    }

    // ---- where the work stands ----------------------------------------------------------------------------------------------

    /**
     * Five stops and the next action, from facts the page already holds:
     *   { status, selected, approval: "none" | "PENDING_APPROVAL" | "APPROVED" | "REJECTED",
     *     run: null | { busy, finished, canDecide, kept, reset, failed }, otherApplied: boolean }
     * `run` is the selected fix's own run; `otherApplied` says a different change is still on the cluster.
     */
    function stages(facts) {
        const f = facts || {};
        const ready = f.status === "ready";
        const loading = f.status === "loading" || f.status === "loading-recommendation";
        const run = f.run || null;
        const out = [];
        const add = (id, label, state, note, target) => out.push({ id, label, state, note, target });

        add("risk", "Understand risk", ready ? "done" : loading ? "current" : f.status === "needs-input" ? "current" : "blocked",
            ready ? "Assessed" : loading ? "Reading the cluster" : f.status === "needs-input" ? "Choose an application" : "Evidence missing", "rec-s-risk");
        add("fix", "Choose a fix", !ready ? "waiting" : f.selected ? "done" : "blocked",
            !ready ? "" : f.selected ? "Selected" : "No fix can be put forward", "rec-s-fix");

        let approve = ["waiting", ""];
        if (ready && f.selected) {
            approve = f.approval === "APPROVED" ? ["done", "Approved"]
                : f.approval === "PENDING_APPROVAL" ? ["current", "Waiting for a decision"]
                : f.approval === "REJECTED" ? ["blocked", "Rejected"]
                : ["current", "Review, then request approval"];
        }
        add("approve", "Review and approve", approve[0], approve[1], f.approval && f.approval !== "none" ? "rec-s-approve" : "rec-s-review");

        let apply = ["waiting", ""];
        if (ready && f.selected && f.approval === "APPROVED") {
            apply = run && run.busy ? ["current", "Running"]
                : run && run.finished ? [run.failed ? "blocked" : "done", run.failed ? "Run stopped" : "Tested"]
                : f.otherApplied ? ["blocked", "Another change is applied"]
                : ["current", "Ready to apply"];
        }
        add("apply", "Apply and test", apply[0], apply[1], run ? "rec-s-result" : "rec-s-approve");

        let decide = ["waiting", ""];
        if (run && run.kept) decide = ["done", "Kept"];
        else if (run && run.reset) decide = ["done", "Reset"];
        else if (run && run.finished && run.canDecide) decide = ["current", "Change is on the cluster"];
        add("decide", "Keep or reset", decide[0], decide[1], "rec-s-result");

        const at = out.find((stage) => stage.state === "current" || stage.state === "blocked") || null;
        const NEXT = {
            risk: null, fix: null,
            approve: { none: "Review the change", PENDING_APPROVAL: "Decide on the request", REJECTED: "Choose another fix" }[f.approval || "none"],
            apply: run && run.busy ? "Follow the run" : f.otherApplied ? "Reset or keep the applied change" : "Apply and test",
            decide: "Keep or reset the change",
        };
        const next = at && NEXT[at.id] ? { label: NEXT[at.id], target: at.id === "approve" && f.approval === "REJECTED" ? "rec-s-fix" : at.target } : null;
        return { stages: out, next };
    }

    const STATE_WORD = { done: "Done", current: "Now", waiting: "Later", blocked: "Needs attention" };
    const STATE_MARK = { done: "✓", blocked: "!" };

    function renderRail(list, model, options) {
        const doc = docOf(options);
        const onGo = (options && options.onGo) || (() => {});
        list.replaceChildren(...model.stages.map((stage, index) => {
            const item = el(doc, "li", undefined, "rx-rail__item", { "data-state": stage.state });
            const button = el(doc, "button", undefined, "rx-rail__stop", { type: "button", "data-stage": stage.id });
            // The state is a mark, a word (for screen readers and the tooltip) and a colour: never colour alone.
            button.setAttribute("aria-label", `${stage.label}: ${stage.note || STATE_WORD[stage.state]}`);
            if (stage.state === "current") button.setAttribute("aria-current", "step");
            button.append(el(doc, "span", STATE_MARK[stage.state] || String(index + 1), "rx-rail__mark", { "aria-hidden": "true" }),
                el(doc, "span", stage.label, "rx-rail__label"), el(doc, "span", stage.note || "", "rx-rail__note"));
            button.addEventListener("click", () => onGo(stage.target));
            item.append(button);
            return item;
        }));
    }

    // ---- 1. the risk ----------------------------------------------------------------------------------------------------------

    const CELL = {
        safe: { mark: "✓", word: "Survives" },
        exposed: { mark: "✕", word: "Stops" },
        fixed: { mark: "✓", word: "Stops today, survives with this fix" },
        still: { mark: "✕", word: "Stops today and with this fix" },
        regressed: { mark: "✕", word: "Survives today, stops with this fix" },
    };

    function cellState(workflow, node, hasAfter) {
        const before = workflow.before.includes(node);
        if (!hasAfter) return before ? "exposed" : "safe";
        const after = workflow.after.includes(node);
        return before ? (after ? "still" : "fixed") : (after ? "regressed" : "safe");
    }

    /**
     * The verdict in one sentence and the survival matrix: one row per workflow, one column per worker node, each cell saying what
     * losing that node does today and, when a fix is selected, with that fix. Returns what it drew, or null when the engine sent no
     * exposure table (then only the engine's own impact line is shown).
     */
    function renderRisk(container, detail, candidate, options) {
        const doc = docOf(options);
        const onNode = options && options.onNode;
        container.replaceChildren();
        const evidence = (detail && detail.evidence) || {};
        const impact = (detail && detail.baselineImpact) || null;
        const model = exposure(detail, candidate);
        const failed = evidence.failedNode || null;
        const total = model.workflows.length;
        const stopping = failed ? model.workflows.filter((w) => w.before.includes(failed)).length : null;

        const head = el(doc, "div", undefined, "rx-verdict");
        const figure = el(doc, "div", undefined, "rx-verdict__figure", { "data-level": impact && impact.blastRadius >= 50 ? "high" : impact && impact.blastRadius > 0 ? "some" : "none" });
        figure.append(el(doc, "span", impact ? percent(impact.blastRadius) : "—", "rx-verdict__number"),
            el(doc, "span", "blast radius today", "rx-verdict__unit"));
        const words = el(doc, "div", undefined, "rx-verdict__words");
        let sentence = "The engine reported no failure impact for this application.";
        if (failed && total) {
            sentence = stopping === 0 ? `If ${failed} stops, every workflow keeps running.`
                : `If ${failed} stops, ${stopping === total ? (total === 1 ? "the workflow stops" : `all ${total} workflows stop`) : `${stopping} of ${plural(total, "workflow")} stop`}.`;
        } else if (failed && impact) {
            sentence = impact.blastRadius > 0 ? `If ${failed} stops, the workflow is affected.` : `If ${failed} stops, the workflow keeps running.`;
        }
        words.append(el(doc, "p", sentence, "rx-verdict__sentence"));
        const why = [];
        if (impact && Array.isArray(impact.failedServices) && impact.failedServices.length) {
            why.push(`${impact.failedServices.join(", ")} would have no running replica left.`);
        }
        if (evidence.failedNodeChosen) why.push(evidence.failedNodeChosen === "highest blast radius" ? "This is the node whose loss hurts most." : `Node chosen: ${evidence.failedNodeChosen}.`);
        if (why.length) words.append(el(doc, "p", why.join(" "), "rx-verdict__why"));
        head.append(figure, words);
        container.append(head);

        if (!total || !model.nodes.length) return null;

        const blast = new Map(((detail && detail.nodeRiskRanking) || []).map((row) => [row.node, row.blastRadius]));
        const scroll = el(doc, "div", undefined, "table-scroll");
        const table = el(doc, "table", undefined, "rx-matrix");
        table.append(el(doc, "caption", model.hasAfter ? "What stops when one worker node is lost: today, and with the selected fix"
            : "What stops when one worker node is lost", "sr-only"));
        const headRow = el(doc, "tr");
        headRow.append(el(doc, "th", "Workflow", "rx-matrix__corner", { scope: "col" }));
        for (const node of model.nodes) {
            const th = el(doc, "th", undefined, "rx-matrix__node", { scope: "col", "data-assessed": String(node === failed) });
            const pick = el(doc, onNode ? "button" : "span", undefined, "rx-matrix__pick");
            if (onNode) {
                pick.setAttribute("type", "button");
                pick.setAttribute("title", node === failed ? "The failure being assessed" : `Assess the loss of ${node}`);
                pick.setAttribute("aria-pressed", String(node === failed));
                pick.addEventListener("click", () => { if (node !== failed) onNode(node); });
            }
            pick.append(el(doc, "span", node, "rx-matrix__name"),
                el(doc, "span", blast.has(node) ? `${percent(blast.get(node))} lost` : "", "rx-matrix__blast"));
            th.append(pick);
            headRow.append(th);
        }
        const thead = el(doc, "thead");
        thead.append(headRow);
        const tbody = el(doc, "tbody");
        const counts = { fixed: 0, still: 0, regressed: 0, exposed: 0, safe: 0 };
        for (const workflow of model.workflows) {
            const row = el(doc, "tr");
            const name = el(doc, "th", undefined, "rx-matrix__workflow", { scope: "row" });
            name.append(el(doc, "span", workflow.name, "rx-matrix__wfname"));
            if (isNumber(workflow.criticality)) name.append(el(doc, "span", `criticality ${workflow.criticality}`, "rx-matrix__crit"));
            row.append(name);
            for (const node of model.nodes) {
                const state = cellState(workflow, node, model.hasAfter);
                counts[state] += 1;
                const cell = el(doc, "td", undefined, "rx-cell", { "data-state": state, "data-assessed": String(node === failed) });
                const inner = el(doc, "span", undefined, "rx-cell__body", { title: `${workflow.name} if ${node} stops: ${CELL[state].word}` });
                if (state === "fixed" || state === "regressed") {
                    inner.append(el(doc, "span", state === "fixed" ? "✕" : "✓", "rx-cell__was", { "aria-hidden": "true" }),
                        el(doc, "span", "→", "rx-cell__arrow", { "aria-hidden": "true" }));
                }
                inner.append(el(doc, "span", CELL[state].mark, "rx-cell__mark", { "aria-hidden": "true" }),
                    el(doc, "span", CELL[state].word, "sr-only"));
                cell.append(inner);
                row.append(cell);
            }
            tbody.append(row);
        }
        table.append(thead, tbody);
        scroll.append(table);
        container.append(scroll);

        const legend = el(doc, "ul", undefined, "rx-legend", { "aria-label": "How to read the table" });
        const key = (state, text) => { const item = el(doc, "li", undefined, "rx-legend__item", { "data-state": state }); item.append(el(doc, "span", CELL[state].mark, "rx-legend__mark", { "aria-hidden": "true" }), el(doc, "span", text)); return item; };
        legend.append(key("safe", "Survives"), key(model.hasAfter ? "still" : "exposed", model.hasAfter ? "Stops, with or without the fix" : "Stops"));
        if (model.hasAfter) legend.append(key("fixed", "Fixed by the selected fix"));
        if (counts.regressed) legend.append(key("regressed", "Made worse by the selected fix"));
        container.append(legend);

        let summary = `Today ${model.safeBefore} of ${plural(total, "workflow")} survive${model.safeBefore === 1 ? "s" : ""} the loss of any single node.`;
        if (model.hasAfter) summary += ` With the selected fix: ${model.safeAfter} of ${total} (predicted).`;
        container.append(el(doc, "p", summary, "rx-matrix__summary"),
            el(doc, "p", "Calculated from where the pods run now. Nothing here was measured by stopping a node.", "rx-fineprint"));
        return { total, stopping, counts, safeBefore: model.safeBefore, safeAfter: model.safeAfter, hasAfter: model.hasAfter };
    }

    // ---- 2. the fixes ---------------------------------------------------------------------------------------------------------

    /** Before and after as two bars and two figures. A prediction of no gain is not drawn in the colour of a gain. */
    function meter(doc, n) {
        const box = el(doc, "div", undefined, "rx-meter", { "data-gain": String(isNumber(n.before) && isNumber(n.after) && n.after < n.before) });
        const bar = (value, kind) => {
            const track = el(doc, "span", undefined, `rx-meter__track rx-meter__track--${kind}`, { "aria-hidden": "true" });
            const fill = el(doc, "span", undefined, "rx-meter__fill");
            fill.style.width = `${isNumber(value) ? Math.max(0, Math.min(100, value)) : 0}%`;
            track.append(fill);
            return track;
        };
        const figures = el(doc, "span", undefined, "rx-meter__figures");
        figures.append(el(doc, "span", percent(n.before), "rx-meter__before"), el(doc, "span", "→", "rx-meter__arrow", { "aria-hidden": "true" }),
            el(doc, "span", " to ", "sr-only"), el(doc, "span", percent(n.after), "rx-meter__after"));
        box.append(figures, bar(n.before, "before"), bar(n.after, "after"));
        return box;
    }

    function partition(detail) {
        const candidates = (detail && Array.isArray(detail.candidates) && detail.candidates) || [];
        const feasible = candidates.filter((c) => c.feasible);
        return { useful: feasible.filter(improves), noGain: feasible.filter((c) => !improves(c)), ruledOut: candidates.filter((c) => !c.feasible) };
    }

    /**
     * The selected fix as the lead card, and the other options as rows to choose from. Options the engine predicts no gain from, and
     * options it ruled out, are behind their own disclosures with the engine's reason.
     * options: { selectedId, engineId, canSelect(candidate), whyNot(candidate), onSelect(id), failedNode }
     */
    function renderFixes(container, detail, options) {
        const doc = docOf(options);
        const o = options || {};
        container.replaceChildren();
        const groups = partition(detail);
        const all = [...groups.useful, ...groups.noGain, ...groups.ruledOut];
        const selected = all.find((c) => c.id === o.selectedId) || null;
        const total = exposure(detail, null).workflows.length;
        const safeWith = (c) => exposure(detail, c).safeAfter;

        const facts = (c) => {
            const list = el(doc, "ul", undefined, "rx-fix__facts");
            const safe = safeWith(c);
            if (safe !== null && total) list.append(el(doc, "li", `${safe} of ${plural(total, "workflow")} survive any node loss`, "rx-fix__fact"));
            const cost = (c.cost && c.cost.resource) || null;
            const costItem = el(doc, "li", cost ? `${String(cost).charAt(0)}${String(cost).slice(1).toLowerCase()} cost` : "Cost not reported", "rx-fix__fact");
            if (c.cost && c.cost.note) costItem.setAttribute("title", c.cost.note);
            list.append(costItem);
            // Filled in by the page from the saved runs the server reports; a fix starts as predicted, not tested.
            list.append(el(doc, "li", "Not tested", "rx-fix__fact rx-fix__evidence", { "data-evidence-for": c.id }));
            return list;
        };

        if (selected) {
            const n = numbers(selected);
            const name = fixName(selected);
            const lead = el(doc, "article", undefined, "rx-lead", { "data-candidate": selected.id });
            const top = el(doc, "div", undefined, "rx-lead__top");
            const title = el(doc, "div", undefined, "rx-lead__title");
            const tags = el(doc, "div", undefined, "rx-tags");
            tags.append(el(doc, "span", "Selected", "rx-tag rx-tag--selected"));
            if (selected.id === o.engineId) tags.append(el(doc, "span", "Top ranked", "rx-tag"));
            title.append(tags, el(doc, "h4", name.title, "rx-lead__name"), el(doc, "p", name.target, "rx-lead__target"));
            const result = el(doc, "div", undefined, "rx-lead__result");
            result.append(el(doc, "span", n.scope === "worst" ? "Worst case, any single node" : `If ${o.failedNode || "the assessed node"} stops`, "rx-lead__scope"),
                meter(doc, n), el(doc, "span", "Predicted by simulation, not measured", "rx-lead__caveat"));
            top.append(title, result);
            lead.append(top);
            if (n.scope === "worst" && isNumber(n.node.after) && o.failedNode) {
                lead.append(el(doc, "p", `If ${o.failedNode} stops: ${percent(n.node.before)} → ${percent(n.node.after)}.`
                    + (n.after > 0 ? " Another node then becomes the worst case; the table above shows which workflows it still stops." : ""), "rx-lead__note"));
            }
            lead.append(facts(selected));
            container.append(lead);
        }

        const row = (c) => {
            const name = fixName(c);
            const item = el(doc, "li", undefined, "rx-fix", { "data-candidate": c.id, "data-feasible": String(!!c.feasible) });
            const text = el(doc, "div", undefined, "rx-fix__text");
            const heading = el(doc, "div", undefined, "rx-fix__heading");
            heading.append(el(doc, "span", name.title, "rx-fix__name"));
            if (c.id === o.engineId) heading.append(el(doc, "span", "Top ranked", "rx-tag"));
            text.append(heading, el(doc, "span", name.target, "rx-fix__target"));
            item.append(text);
            if (c.feasible) {
                item.append(meter(doc, numbers(c)), facts(c));
                const allowed = !o.canSelect || o.canSelect(c);
                const choose = el(doc, "button", "Select", "secondary-button rx-fix__choose", { type: "button", "data-select": c.id });
                if (!allowed) {
                    choose.disabled = true;
                    choose.setAttribute("title", (o.whyNot && o.whyNot(c)) || "This fix cannot be put forward.");
                } else if (o.onSelect) {
                    choose.addEventListener("click", () => o.onSelect(c.id));
                }
                item.append(choose);
            } else {
                const reason = (c.approval && c.approval.reason) || (c.feasibility && c.feasibility.explanation) || "The engine could not verify a placement for this change.";
                item.append(el(doc, "p", reason, "rx-fix__reason"));
            }
            return item;
        };
        const listOf = (candidates, className) => {
            const list = el(doc, "ul", undefined, `rx-fixes ${className || ""}`.trim());
            list.append(...candidates.map(row));
            return list;
        };
        const others = groups.useful.filter((c) => c.id !== o.selectedId);
        if (others.length) {
            container.append(el(doc, "h4", selected ? "Other options" : "Options", "rx-subhead"), listOf(others));
        }
        const fold = (candidates, label, note) => {
            const list = candidates.filter((c) => c.id !== o.selectedId);
            if (!list.length) return;
            const box = el(doc, "details", undefined, "rx-fold");
            box.append(el(doc, "summary", `${list.length} ${label}`));
            if (note) box.append(el(doc, "p", note, "rx-fineprint"));
            box.append(listOf(list, "rx-fixes--quiet"));
            container.append(box);
        };
        fold(groups.noGain, groups.noGain.length === 1 ? "option with no predicted gain" : "options with no predicted gain",
            "Feasible, but the simulation predicts the same blast radius: on their own they do not remove a single point of failure.");
        fold(groups.ruledOut, groups.ruledOut.length === 1 ? "option ruled out" : "options ruled out", null);
        if (!all.length) container.append(el(doc, "p", "The engine generated no candidate change. That is not a finding that the application is resilient.", "rx-empty"));
        return { useful: groups.useful.length, noGain: groups.noGain.length, ruledOut: groups.ruledOut.length, selected: selected ? selected.id : null };
    }

    // ---- 3. the change ----------------------------------------------------------------------------------------------------------

    /** What the selected fix edits, Deployment by Deployment, in words; then what to know before applying it. */
    function renderReview(container, detail, candidate, options) {
        const doc = docOf(options);
        container.replaceChildren();
        if (!candidate) {
            container.append(el(doc, "p", "Select a fix to see exactly what it changes.", "rx-empty"));
            return null;
        }
        const changes = Array.isArray(candidate.proposedChanges) ? candidate.proposedChanges : [];
        let edited = 0;
        let added = 0;
        if (changes.length) {
            const grid = el(doc, "div", undefined, "rx-diff");
            for (const change of changes) {
                const fields = (change.fields || []).filter((field) => field.changed !== false);
                if (!fields.length) continue;
                edited += 1;
                const card = el(doc, "div", undefined, "rx-diff__card");
                card.append(el(doc, "div", change.deployment, "rx-diff__name"), el(doc, "div", `Deployment in ${change.namespace}`, "rx-diff__kind"));
                const list = el(doc, "dl", undefined, "rx-diff__fields");
                for (const field of fields) {
                    if (field.field === "replicas" && isNumber(field.from) && isNumber(field.to)) added += Math.max(0, field.to - field.from);
                    const value = el(doc, "dd", undefined, "rx-diff__value");
                    value.append(el(doc, "span", field.fromText === null || field.fromText === undefined ? "not recorded" : field.fromText, "rx-diff__from"),
                        el(doc, "span", "→", "rx-diff__arrow", { "aria-hidden": "true" }), el(doc, "span", " becomes ", "sr-only"),
                        el(doc, "span", field.toText === null || field.toText === undefined ? "not recorded" : field.toText, "rx-diff__to"));
                    list.append(el(doc, "dt", field.label, "rx-diff__label"), value);
                }
                card.append(list);
                grid.append(card);
            }
            container.append(grid);
        } else if (candidate.recommendedChange) {
            container.append(el(doc, "p", candidate.recommendedChange, "rx-review__plain"));
        }
        const facts = [];
        if (edited) facts.push(`${plural(edited, "Deployment")} edited`);
        if (changes.length) facts.push(added ? `${plural(added, "pod")} added` : "no pods added");
        if (edited) facts.push("each edited Deployment rolls out new pods");
        if (facts.length) container.append(el(doc, "p", `${facts.join(" · ")}.`, "rx-review__facts"));

        const proposal = candidate.yamlProposal || null;
        const notes = [...((proposal && proposal.disclosures) || [])];
        const rollout = candidate.feasibility && candidate.feasibility.rollout;
        if (rollout && rollout.reason) notes.push(rollout.reason);
        if (notes.length) {
            const box = el(doc, "details", undefined, "rx-fold");
            box.append(el(doc, "summary", `${plural(notes.length, "thing")} to know before applying`));
            const list = el(doc, "ul", undefined, "rx-notes");
            list.append(...notes.map((text) => el(doc, "li", text)));
            box.append(list);
            container.append(box);
        }
        return { edited, added, notes: notes.length };
    }

    global.ResilienceJourney = { stages, renderRail, renderRisk, renderFixes, renderReview, numbers, improves, fixName, strategyTitle, exposure, partition, cellState };
})(typeof window !== "undefined" ? window : globalThis);
