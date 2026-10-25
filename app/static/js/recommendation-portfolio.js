/*
 * The "across all workflows" panel of the Recommendations page. Owned by: Member 4 (L.A. Nemal).
 *
 * A cluster runs several workflows, and the point of a fix is to keep them all alive when a worker fails, or at least the ones that
 * matter most. This panel asks the backend for ONE evaluation across every workflow (GET /api/recommendations/portfolio/detail),
 * weighted by each workflow's criticality, and lists the changes best first, each with the workflows it protects and the ones it
 * leaves exposed. The person picks a fix here; nothing is approved here.
 *
 * Approval is for one change under one named workflow, so choosing a fix hands over to the page's ordinary review: the person says which
 * workflow to review it for (the ones it protects are offered, most critical first) and the page continues there. A combined fix is
 * several changes, so it is reviewed part by part, each under a workflow it protects.
 *
 * This file draws what the server returns and computes no research value: percentages and workflow lists are printed as given, an
 * unknown stays unknown, and a workflow the server could not evaluate is named with its reason, never left out silently. Every answer is
 * tagged with the request that asked for it, and an answer for an older request is dropped. All text goes through textContent.
 */
(function () {
    "use strict";

    const URL_BASE = "/api/recommendations/portfolio/detail";
    const SHOWN = 4;                                   // best fixes shown at once; the rest sit under "Other changes"
    const SOURCE_LABEL = {
        frozen: { text: "Approved frozen workflows", variant: "success" },
        declared: { text: "Declared workflows · research only", variant: "warning" },
    };
    const text = (value, max) => (typeof value === "string" ? value.slice(0, max) : "");
    const list = (value, max) => (Array.isArray(value) ? value.slice(0, max) : []);

    /**
     * Which source the panel evaluates, or null when "across all workflows" has nothing to say. It needs at least two usable workflows of
     * one kind: with one the ordinary single-workflow review is the same answer. Frozen and declared are never mixed, and declared ones
     * are used only when no frozen workflow can be, because only a frozen workflow can ever reach the production gate.
     */
    function pickSource(workflows) {
        const usable = (workflows || []).filter((workflow) => workflow && workflow.applicable);
        const frozen = usable.filter((workflow) => !workflow.declared);
        const declared = usable.filter((workflow) => workflow.declared);
        if (frozen.length >= 2) return "frozen";
        if (!frozen.length && declared.length >= 2) return "declared";
        return null;
    }

    function portfolioUrl(source, namespace, failedNode) {
        const params = new URLSearchParams({ source, namespace });
        if (failedNode) params.set("failed_node", failedNode);
        return `${URL_BASE}?${params.toString()}`;
    }

    const criticalityOf = (detail, workflowId) => {
        const found = list(detail && detail.portfolio && detail.portfolio.workflows, 100).find((w) => w.workflowId === workflowId);
        return found ? Number(found.criticality) : -1;
    };

    /**
     * The workflows a part can be reviewed for: the ones it protects on its own plus, for a combined fix, the ones the whole fix protects
     * (a workflow that needs both changes is protected by neither alone). Most critical first; with none, every evaluated workflow.
     */
    function reviewOptions(part, detail, candidate) {
        const evaluated = list(detail && detail.portfolio && detail.portfolio.workflows, 100).map((w) => w.workflowId);
        const wanted = new Set([...list(part && part.protects, 100), ...list(candidate && candidate.workflows && candidate.workflows.protected, 100)]);
        const protects = evaluated.filter((id) => wanted.has(id));
        const pool = protects.length ? protects : evaluated;
        return [...pool].sort((a, b) => criticalityOf(detail, b) - criticalityOf(detail, a) || (a < b ? -1 : 1));
    }

    function usable(candidate) {
        return !!candidate && candidate.feasible === true && candidate.ranking && candidate.ranking.rank !== null && candidate.ranking.rank !== undefined;
    }

    /** The ranked, feasible changes best first, then every other change, in the server's order. */
    function ordered(detail) {
        const all = list(detail && detail.candidates, 200);
        const ranked = all.filter(usable).sort((a, b) => a.ranking.rank - b.ranking.rank);
        return { ranked, others: all.filter((candidate) => !usable(candidate)) };
    }

    /** One sentence on the lead fix, from what the server says it protects. null when no change helps. */
    function headline(detail) {
        const { ranked } = ordered(detail);
        const total = list(detail && detail.portfolio && detail.portfolio.workflows, 100).length;
        const lead = ranked[0];
        if (!lead) return null;
        const protectedCount = list(lead.workflows && lead.workflows.protected, 100).length;
        const exposed = list(lead.workflows && lead.workflows.exposed, 100);
        if (lead.coverage && lead.coverage.protectsEveryWorkflow) {
            return { kind: "all", lead, text: `Best fix: protects all ${total} workflows against the loss of any one worker.` };
        }
        const left = exposed.length ? ` ${exposed.join(", ")} would still break if the wrong worker fails.` : "";
        return { kind: "some", lead, text: `Best fix: protects ${protectedCount} of ${total} workflows.${left}` };
    }

    function createPortfolioPanel(env) {
        const { root, document, fetchJson, onReview } = env;
        const names = env.strategyName || ((code) => String(code || "").replace(/_/g, " ").toLowerCase());
        const s = { key: null, generation: 0, status: "idle", detail: null, error: null, source: null, open: null };

        const make = (tag, content, className) => {
            const node = document.createElement(tag);
            if (content !== undefined && content !== null) node.textContent = content;
            if (className) node.className = className;
            return node;
        };

        function chips(ids, className) {
            const wrap = make("span", null, "rec-portfolio__chips");
            list(ids, 50).forEach((id) => wrap.append(make("span", text(id, 80), `rec-portfolio__chip ${className || ""}`.trim())));
            return wrap;
        }

        function line(label, node) {
            const row = make("div", null, "rec-portfolio__line");
            row.append(make("span", label, "rec-portfolio__label"), node);
            return row;
        }

        function partName(part) {
            return `${names(part.type)} on ${text(part.service, 80)}`;
        }

        function reviewRow(part, detail, candidate) {
            const options = reviewOptions(part, detail, candidate);
            const row = make("div", null, "rec-portfolio__review");
            row.append(make("span", `Review ${partName(part)} for`, "rec-portfolio__review-name"));
            const select = make("select", null, "rec-inputs__input rec-portfolio__select");
            select.setAttribute("aria-label", `Workflow to review ${partName(part)} for`);
            options.forEach((id) => {
                const option = make("option", id);
                option.value = id;
                select.append(option);
            });
            const button = make("button", "Review", "secondary-button");
            button.setAttribute("type", "button");
            if (!options.length) button.disabled = true;
            button.addEventListener("click", () => {
                if (select.value && onReview) onReview({ workflowId: select.value, source: s.source, service: part.service, type: part.type });
            });
            row.append(select, button);
            return row;
        }

        function card(candidate, detail, position) {
            const box = make("article", null, "rec-portfolio__card");
            box.setAttribute("data-candidate", text(candidate.id, 200));
            if (position === 0) box.setAttribute("data-lead", "true");
            const head = make("div", null, "rec-portfolio__card-head");
            const parts = list(candidate.parts, 5);
            const title = candidate.type === "COMBINATION"
                ? parts.map(partName).join(" + ")
                : `${names(candidate.type)} on ${text(candidate.service, 80)}`;
            head.append(make("strong", title, "rec-portfolio__title"));
            const all = candidate.coverage && candidate.coverage.protectsEveryWorkflow;
            head.append(make("span", all ? "Protects every workflow" : `Protects ${list(candidate.workflows && candidate.workflows.protected, 100).length} of ${list(detail.portfolio.workflows, 100).length}`,
                `rec-portfolio__verdict${all ? " rec-portfolio__verdict--all" : ""}`));
            box.append(head);
            const protectedIds = list(candidate.workflows && candidate.workflows.protected, 100);
            box.append(line("Protects", protectedIds.length ? chips(protectedIds, "rec-portfolio__chip--ok") : make("span", "none on its own", "rec-portfolio__muted")));
            const exposed = candidate.workflows ? candidate.workflows.exposed : null;
            if (exposed === null || exposed === undefined) {
                box.append(line("Still exposed", make("span", "not known", "rec-portfolio__muted")));
            } else if (exposed.length) {
                const at = candidate.workflows.exposedAt || {};
                const wrap = chips(exposed, "rec-portfolio__chip--warn");
                wrap.append(make("span", exposed.map((id) => `${id} if ${list(at[id], 20).join(" or ")} fails`).join("; "), "rec-portfolio__muted"));
                box.append(line("Still exposed", wrap));
            } else {
                box.append(line("Still exposed", make("span", "nothing", "rec-portfolio__muted")));
            }
            if (candidate.coverage) {
                box.append(line("Worst single failure", make("span", `${candidate.coverage.worstCaseBefore}% → ${candidate.coverage.worstCaseAfter}%`)));
            }
            const cost = candidate.cost && candidate.cost.resource;
            if (cost) box.append(line("Cost", make("span", `${String(cost).charAt(0)}${String(cost).slice(1).toLowerCase()}`)));
            const actions = make("div", null, "rec-portfolio__actions");
            const choose = make("button", s.open === candidate.id ? "Hide review options" : "Use this fix", "secondary-button");
            choose.setAttribute("type", "button");
            choose.setAttribute("aria-expanded", String(s.open === candidate.id));
            choose.addEventListener("click", () => { s.open = s.open === candidate.id ? null : candidate.id; draw(); });
            actions.append(choose);
            box.append(actions);
            if (s.open === candidate.id) {
                const review = make("div", null, "rec-portfolio__reviews");
                review.append(make("p", parts.length > 1
                    ? "This fix is more than one change. Approval is for one change under one workflow, so review each part for a workflow it protects."
                    : "Approval is for one change under one workflow. Choose the workflow to review it for.", "rec-portfolio__note"));
                parts.forEach((part) => review.append(reviewRow(part, detail, candidate)));
                box.append(review);
            }
            return box;
        }

        function draw() {
            root.replaceChildren();
            if (s.status === "idle") { root.hidden = true; return; }
            root.hidden = false;
            const head = make("header", null, "rec-portfolio__head");
            head.append(make("h4", "Across all workflows", "rec-portfolio__heading"));
            const label = SOURCE_LABEL[s.source];
            if (label) head.append(make("span", label.text, `status-badge status-badge--${label.variant} status-badge--no-dot`));
            root.append(head);
            if (s.status === "loading") { root.append(make("p", "Evaluating every workflow together…", "ui-note")); return; }
            if (s.status === "error") {
                const error = s.error || {};
                const note = make("p", `${text(error.message, 400) || "The evaluation across all workflows failed."}${error.code ? ` (${text(error.code, 80)})` : ""}`, "approval-note");
                note.setAttribute("role", "alert");
                root.append(note);
                return;
            }
            const detail = s.detail;
            const workflows = list(detail.portfolio && detail.portfolio.workflows, 100);
            root.append(make("p", `${workflows.length} workflows weighed by criticality: ${workflows.map((w) => `${text(w.workflowId, 80)} (${w.criticality})`).join(", ")}.`, "rec-portfolio__sub"));
            list(detail.portfolio && detail.portfolio.notEvaluated, 20).forEach((item) => {
                root.append(make("p", `Not evaluated: ${text(item.workflowId, 80)}. ${text(item.message, 300)}`, "rec-portfolio__warn"));
            });
            const { ranked, others } = ordered(detail);
            const lead = headline(detail);
            if (!lead) {
                root.append(make("p", "No change was found that reduces the risk across these workflows.", "rec-portfolio__lead"));
            } else {
                root.append(make("p", lead.text, `rec-portfolio__lead rec-portfolio__lead--${lead.kind}`));
            }
            const body = make("div", null, "rec-portfolio__list");
            ranked.slice(0, SHOWN).forEach((candidate, index) => body.append(card(candidate, detail, index)));
            root.append(body);
            const more = ranked.slice(SHOWN).concat(others);
            if (more.length) {
                const details = make("details", null, "rec-portfolio__more");
                details.append(make("summary", `Other changes (${more.length})`));
                more.forEach((candidate) => {
                    const row = make("p", `${names(candidate.type)} on ${text(candidate.service, 80)}: ${candidate.feasible ? "no predicted improvement" : text(candidate.feasibility && candidate.feasibility.explanation, 200) || "not feasible"}.`, "rec-portfolio__other");
                    details.append(row);
                });
                root.append(details);
            }
            root.append(make("p", detail.portfolio && detail.portfolio.note ? text(detail.portfolio.note, 500) : "", "rec-portfolio__note"));
        }

        async function load(source, namespace, failedNode) {
            const key = `${source}|${namespace}|${failedNode || ""}`;
            if (key === s.key && (s.status === "ready" || s.status === "loading")) return;
            const generation = ++s.generation;
            s.key = key; s.source = source; s.status = "loading"; s.detail = null; s.error = null; s.open = null;
            draw();
            try {
                // One full evaluation per workflow and node runs on the server, so this is allowed longer than a single recommendation.
                const detail = await fetchJson(portfolioUrl(source, namespace, failedNode));
                if (generation !== s.generation) return;
                s.detail = detail; s.status = "ready";
            } catch (error) {
                if (generation !== s.generation) return;
                s.error = { code: error && error.code, message: error && error.message }; s.status = "error";
            }
            draw();
        }

        /** Nothing to show: an answer still on its way is dropped when it arrives. */
        function clear() {
            s.generation += 1; s.key = null; s.status = "idle"; s.detail = null; s.error = null; s.open = null;
            draw();
        }

        draw();
        return { load, clear, state: () => ({ ...s }) };
    }

    window.ResilienceRecommendationPortfolio = { createPortfolioPanel, pickSource, portfolioUrl, reviewOptions, headline };
})();
