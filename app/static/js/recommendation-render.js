/*
 * Recommendation display components (Member 4's area).
 *
 * Pure functions that render what they are given: recommendation candidates and their ranking, the
 * predicted-versus-observed validation panel, the generated YAML proposal, and current versus predicted
 * placement. They fetch nothing and calculate no research value. Every kind of missing evidence has an
 * explicit state, and nothing is estimated, defaulted or derived in the browser. In particular a predicted
 * placement is shown only when the recommendation engine supplied one for a valid DeploymentGraph.
 *
 * `context` carries the optional evidence that sits next to a RecommendationReport:
 *   baselineBlastRadius  number | null    percent, from a failure-impact report
 *   validation           object | null    a ValidationReport
 *   yaml                 string | null    a generated Kubernetes YAML proposal
 *   currentDeployment    object | null    the topology the candidates were evaluated on (a DeploymentGraph)
 *   simulatedDeployment  object | null    the engine's virtual result (a DeploymentGraph)
 *
 * All text goes through textContent, so no value from the API is ever interpreted as markup.
 */
(function () {
    const MISSING = "—";

    function percent(value) {
        return value === null || value === undefined || Number.isNaN(Number(value))
            ? MISSING
            : `${Number(value).toFixed(0)}%`;
    }

    function ratioPercent(value) {
        return value === null || value === undefined || Number.isNaN(Number(value))
            ? MISSING
            : `${(Number(value) * 100).toFixed(0)}%`;
    }

    function codeLabel(code) {
        if (!code) return MISSING;
        return String(code)
            .split("_")
            .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
            .join(" ");
    }

    /** "TOPOLOGY_SPREAD" -> "Topology spread": Docker Desktop writes sentence case. */
    function strategyName(code) {
        const label = codeLabel(code);
        return label === MISSING ? label : label.charAt(0) + label.slice(1).toLowerCase();
    }

    /** "LOW" -> "Low cost". A cost the engine did not report is said to be unreported, never guessed. */
    function costLabel(value) {
        if (value === null || value === undefined || value === "") return "Cost not reported";
        const word = String(value).toLowerCase();
        return `${word.charAt(0).toUpperCase()}${word.slice(1)} cost`;
    }

    /** A share of 0-100 as a bar width. Only for drawing: the figure itself is printed beside the bar, as the engine gave it. */
    function barWidth(value) {
        const number = Number(value);
        if (value === null || value === undefined || Number.isNaN(number)) return "0%";
        return `${Math.max(0, Math.min(100, number))}%`;
    }

    function documentOf(options) {
        return (options && options.document) || window.document;
    }

    function element(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function missingState(doc, title, message) {
        const state = element(doc, "div", undefined, "state empty-state", { role: "status" });
        state.append(element(doc, "p", title, "state__title"), element(doc, "p", message));
        return state;
    }

    // ---- ranking and summary -------------------------------------------------------------------------------

    /** Feasible candidates by descending reported score, then infeasible ones unranked. Ties keep engine order. */
    function rankCandidates(report) {
        const candidates = (report && report.recommendations) || [];
        const selectedId = report ? report.selectedRecommendationId : null;
        const indexed = candidates.map((candidate, index) => ({ candidate, index }));
        const feasible = indexed
            .filter((item) => item.candidate.feasible)
            .sort((a, b) => b.candidate.score - a.candidate.score || a.index - b.index);
        const infeasible = indexed.filter((item) => !item.candidate.feasible);
        return [
            ...feasible.map((item, position) => ({ candidate: item.candidate, rank: position + 1, selected: item.candidate.id === selectedId })),
            ...infeasible.map((item) => ({ candidate: item.candidate, rank: null, selected: item.candidate.id === selectedId })),
        ];
    }

    function selectedCandidate(report) {
        const candidates = (report && report.recommendations) || [];
        return candidates.find((item) => item.id === report.selectedRecommendationId) || candidates[0] || null;
    }

    function summarise(report, context) {
        const candidates = (report && report.recommendations) || [];
        const feasible = candidates.filter((item) => item.feasible);
        const selected = selectedCandidate(report);
        const baseline = context && context.baselineBlastRadius;
        return {
            baseline: baseline === undefined ? null : baseline,
            bestPredicted: feasible.length ? Math.min(...feasible.map((item) => item.predictedBlastRadius)) : null,
            selectedRiskReduction: selected ? selected.riskReduction : null,
            feasibleCount: feasible.length,
            total: candidates.length,
        };
    }

    // ---- the blast-radius gauge: the one mark the page uses for "how bad now, how bad after" ---------------------------------

    /**
     * Two bars, now and predicted, each with its figure printed beside it. ``before`` null means the baseline was not supplied: only the
     * predicted bar is drawn and the page says so, instead of drawing a "before" it does not have.
     */
    function gauge(doc, before, after, label, names) {
        const hasBefore = before !== null && before !== undefined;
        const wrap = element(doc, "div", undefined, "gauge", {
            role: "img",
            "aria-label": `${label || "Blast radius"}: ${hasBefore ? `${percent(before)} now, ` : names ? "" : "baseline not supplied, "}${percent(after)}${names ? "" : " predicted"}`,
        });
        const row = (name, value, kind) => {
            const line = element(doc, "div", undefined, "gauge__row");
            const track = element(doc, "span", undefined, "gauge__track", { "aria-hidden": "true" });
            const fill = element(doc, "span", undefined, `gauge__fill gauge__fill--${kind}`);
            fill.style.width = barWidth(value);
            track.append(fill);
            line.append(element(doc, "span", name, "gauge__name"), track, element(doc, "span", percent(value), "gauge__value"));
            return line;
        };
        if (hasBefore) wrap.append(row("Now", before, "now"));
        // The predicted bar is green only when it is shorter than the one before it: a change that predicts no gain is not drawn as one.
        const improves = hasBefore && Number(after) < Number(before);
        wrap.append(row((names && names.after) || "Predicted", after, names || (hasBefore && !improves) ? "now" : "after"));
        return wrap;
    }

    // ---- candidate cards -------------------------------------------------------------------------------------

    function fact(doc, label, value, className) {
        const wrapper = element(doc, "div", undefined, `candidate-card__fact${className ? ` ${className}` : ""}`);
        wrapper.append(element(doc, "span", label, "candidate-card__fact-label"), element(doc, "span", value, "candidate-card__fact-value"));
        return wrapper;
    }

    function chipOf(doc, text, variant, className) {
        return element(doc, "span", text, `status-badge status-badge--${variant}${className ? ` ${className}` : ""}`);
    }

    /**
     * One card per candidate: what it is (strategy and target), whether it can be put forward, what it is predicted to do and what it
     * costs, in that order. The longer explanation sits behind "Details". The selected candidate is the lead card; ``options.hero``
     * adds the problem statement, the validation status and the next actions to it (the plain view's whole answer in one place).
     *   options.descriptions[id]  the engine's description of the change
     *   options.reasons[id]       the engine's reason a candidate cannot be put forward
     *   options.hero              { problem: string, validationText: string } for the selected card
     */
    function renderCandidateCards(container, report, context, options) {
        const doc = documentOf(options);
        const ranked = rankCandidates(report);
        if (!ranked.length) {
            container.replaceChildren(
                missingState(doc, "No recommendation candidates", "The recommendation engine produced no candidates for this evidence.")
            );
            return;
        }
        const baseline = context && context.baselineBlastRadius;
        const hasBaseline = baseline !== null && baseline !== undefined;
        const cards = ranked.map(({ candidate, rank, selected }) => {
            const lead = selected && !!(options && options.hero);
            const card = element(doc, "article", undefined,
                `candidate-card${selected ? " candidate-card--selected" : ""}${lead ? " candidate-card--lead" : ""}${candidate.feasible ? "" : " candidate-card--blocked"}`,
                { "data-feasible": String(!!candidate.feasible) });

            const identity = element(doc, "div", undefined, "candidate-card__heading");
            const name = element(doc, "div", undefined, "candidate-card__id");
            name.append(
                element(doc, "div", strategyName(candidate.type), "candidate-card__title"),
                element(doc, "div", `on ${candidate.service}`, "candidate-card__target")
            );
            identity.append(element(doc, "span", undefined, "candidate-card__icon", { "data-kind": String(candidate.type || "").toLowerCase(), "aria-hidden": "true" }), name);
            const chips = element(doc, "div", undefined, "candidate-card__chips");
            if (selected) chips.append(chipOf(doc, "Recommended", "info"));
            chips.append(chipOf(doc, candidate.feasible ? "Feasible" : "Not feasible", candidate.feasible ? "success" : "danger"));
            if (rank !== null) chips.append(chipOf(doc, `Rank ${rank}`, "muted", "rec-research-only"));
            const header = element(doc, "div", undefined, "candidate-card__header");
            header.append(identity, chips);
            card.append(header);

            if (lead && options.hero.problem) {
                const problem = element(doc, "p", undefined, "candidate-card__problem");
                problem.append(element(doc, "span", "Problem", "candidate-card__fact-label"), element(doc, "span", options.hero.problem));
                card.append(problem);
            }

            // The predicted improvement: the gauge, and the change in points beside it.
            const effect = element(doc, "div", undefined, "candidate-card__effect");
            effect.append(gauge(doc, hasBaseline ? baseline : null, candidate.predictedBlastRadius));
            const gains = candidate.riskReduction > 0;
            effect.append(element(doc, "div", gains ? `−${candidate.riskReduction} pp` : "No change", `candidate-card__delta${gains ? "" : " candidate-card__delta--none"}`,
                { title: "Predicted change in blast radius, in percentage points" }));
            card.append(effect);

            const facts = element(doc, "div", undefined, "candidate-card__facts");
            facts.append(fact(doc, "Cost", costLabel(candidate.resourceCost)));
            facts.append(fact(doc, "Confidence", ratioPercent(candidate.confidence), "rec-research-only"));
            facts.append(fact(doc, "Score", ratioPercent(candidate.score), "rec-research-only"));
            card.append(facts);

            if (lead && options.hero.validationText !== undefined) {
                const validation = element(doc, "p", undefined, "candidate-card__validation");
                validation.append(element(doc, "span", "Validation", "candidate-card__fact-label"),
                    element(doc, "span", options.hero.validationText, "candidate-card__validation-text"));
                card.append(validation);
            }

            // Everything longer than a line lives behind one disclosure.
            const description = options && options.descriptions && options.descriptions[candidate.id];
            const reason = options && options.reasons && options.reasons[candidate.id];
            if (description || reason || !hasBaseline) {
                const more = element(doc, "details", undefined, "candidate-card__more");
                more.append(element(doc, "summary", candidate.feasible ? "Details" : "Why not feasible"));
                if (reason) more.append(element(doc, "p", reason, "candidate-card__reason"));
                if (description) more.append(element(doc, "p", description, "candidate-card__description"));
                if (!hasBaseline) {
                    more.append(element(doc, "p", "Baseline not provided: the blast radius before the change is shown only when a failure-impact result accompanies the recommendation.", "ui-note"));
                }
                card.append(more);
            }
            return card;
        });
        // The lead card, then the rest. Many alternatives sit behind one disclosure so the page stays a short answer, not a catalogue.
        const lead = cards.filter((card) => /candidate-card--lead/.test(card.className));
        const rest = cards.filter((card) => !/candidate-card--lead/.test(card.className));
        if (!lead.length || rest.length <= 2) {
            container.replaceChildren(...cards);
            return;
        }
        const others = element(doc, "details", undefined, "rec-others");
        others.append(element(doc, "summary", `Other options (${rest.length})`), ...rest);
        container.replaceChildren(...lead, others);
    }

    // ---- ranking table ---------------------------------------------------------------------------------------

    function renderRankingTable(tbody, report, options) {
        const doc = documentOf(options);
        const ranked = rankCandidates(report);
        if (!ranked.length) {
            const row = element(doc, "tr");
            row.append(element(doc, "td", "No candidates to rank.", undefined, { colspan: "8" }));
            tbody.replaceChildren(row);
            return;
        }
        tbody.replaceChildren(
            ...ranked.map(({ candidate, rank, selected }) => {
                const row = element(doc, "tr");
                const label = `${strategyName(candidate.type)} · ${candidate.service}${selected ? " (recommended)" : ""}`;
                for (const value of [
                    rank === null ? MISSING : String(rank),
                    label,
                    candidate.feasible ? "Yes" : "No",
                    percent(candidate.predictedBlastRadius),
                    `${candidate.riskReduction} pp`,
                    candidate.resourceCost,
                    ratioPercent(candidate.confidence),
                    ratioPercent(candidate.score),
                ]) {
                    row.append(element(doc, "td", value));
                }
                return row;
            })
        );
    }

    // ---- validation ------------------------------------------------------------------------------------------

    function barStat(doc, value, label, colour, fillClass) {
        const wrapper = element(doc, "div", undefined, "impact-stat");
        const figure = element(doc, "div", value === null || value === undefined ? "Not observed yet" : percent(value), "impact-stat__value");
        figure.style.color = colour;
        const bar = element(doc, "div", undefined, "progress-bar");
        const fill = element(doc, "div", undefined, `progress-bar__fill${fillClass ? ` ${fillClass}` : ""}`);
        fill.style.width = value === null || value === undefined ? "0%" : percent(value);
        bar.append(fill);
        wrapper.append(figure, element(doc, "div", label, "impact-stat__label"), bar);
        return wrapper;
    }

    function renderValidation(container, validation, options) {
        const doc = documentOf(options);
        if (!validation) {
            container.replaceChildren(
                missingState(
                    doc,
                    "No post-deployment validation evidence",
                    "Observed results appear only after an approved change has been applied and measured in a test environment. Nothing is displayed until then."
                )
            );
            return;
        }
        const observed = validation.observedBlastRadius;
        const wrapper = element(doc, "div");
        const compare = element(doc, "div", undefined, "validation-compare");
        compare.append(
            barStat(doc, validation.predictedBlastRadius, "Predicted Blast Radius", "var(--color-info)"),
            barStat(doc, observed, "Observed Blast Radius", "var(--color-warning)", "progress-bar__fill--warning")
        );
        wrapper.append(compare);
        if (observed !== null && observed !== undefined && validation.predictionError !== null && validation.predictionError !== undefined) {
            wrapper.append(
                element(doc, "p", `The observed result differs from the prediction by ${validation.predictionError} percentage points.`)
            );
            wrapper.append(
                element(doc, "span", `Validation ${validation.validated ? "Passed" : "Failed"}`,
                    `status-badge status-badge--${validation.validated ? "success" : "danger"}`)
            );
        } else {
            wrapper.append(element(doc, "span", "Validation pending", "status-badge status-badge--muted"));
        }
        container.replaceChildren(wrapper);
    }

    // ---- generated YAML --------------------------------------------------------------------------------------

    function renderYaml(container, yamlText, options) {
        const doc = documentOf(options);
        if (!yamlText) {
            container.replaceChildren(
                missingState(
                    doc,
                    "No Kubernetes YAML proposal has been generated",
                    "A proposal is written only for a recommendation that can be approved (verified feasible and ranked), and none is selected. Changes are never applied automatically: a developer must approve a proposal first."
                )
            );
            return;
        }
        const wrapper = element(doc, "div");
        wrapper.append(
            element(doc, "p", "Proposal only — not applied to any cluster. Approval is required before any change.", "ui-note"),
            element(doc, "pre", yamlText)
        );
        container.replaceChildren(wrapper);
    }

    // ---- placement -------------------------------------------------------------------------------------------

    function podRows(doc, graph, serviceName, heading) {
        const wrapper = element(doc, "div");
        const service = ((graph && graph.services) || []).find((item) => item.name === serviceName);
        if (!service) {
            wrapper.append(
                missingState(doc, `Service '${serviceName}' not found in the supplied topology`, `The supplied topology has no service named '${serviceName}'.`)
            );
            return wrapper;
        }
        if (heading) wrapper.append(element(doc, "p", heading, "ui-note"));
        for (const pod of service.pods) {
            const row = element(doc, "div", undefined, "placement-row");
            row.append(element(doc, "span", pod.name), element(doc, "span", pod.node));
            wrapper.append(row);
        }
        return wrapper;
    }

    function renderPlacement(currentContainer, predictedContainer, candidate, context, options) {
        const doc = documentOf(options);
        const service = candidate ? candidate.service : null;
        const current = context && context.currentDeployment;
        const predicted = context && context.simulatedDeployment;

        currentContainer.replaceChildren(
            current && service
                ? podRows(doc, current, service)
                : missingState(doc, "No topology evidence", "The topology the candidates were evaluated on was not supplied.")
        );
        predictedContainer.replaceChildren(
            predicted && service
                ? podRows(doc, predicted, service, "Predicted (virtual), not observed")
                : missingState(
                    doc,
                    "No predicted placement",
                    "A predicted placement must come from the recommendation engine's simulation of a valid DeploymentGraph. This page never estimates one."
                )
        );
    }

    // ---- explained recommendation: evidence checklist, outcome and per-candidate detail -------------------------------
    // Everything below renders what GET /api/recommendations/live/detail returned. Nothing is calculated here: a value
    // the server did not report is shown as not reported, and "ready for approval" is the server's own verdict.

    const STATE_MARK = { available: "✔", pending: "…", incomplete: "!", missing: "✖" };

    /** One line per piece of evidence a recommendation needs, with the exact next action when it is missing. */
    function renderEvidenceChecklist(list, items, options) {
        const doc = documentOf(options);
        list.replaceChildren(...(items || []).map((item) => {
            const row = element(doc, "li", undefined, "rec-evidence__item", { "data-state": item.state });
            row.append(
                element(doc, "strong", `${STATE_MARK[item.state] || "?"} ${item.label}`, "rec-evidence__label"),
                element(doc, "span", ` ${item.detail}`, "rec-evidence__detail")
            );
            if (item.nextAction && item.state !== "available") {
                row.append(element(doc, "div", `Next: ${item.nextAction}`, "rec-evidence__next"));
            }
            return row;
        }));
    }

    /** The overall result. "No candidates" is deliberately worded so that it cannot be read as "healthy". */
    function renderOutcome(container, outcome, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        if (!outcome) return;
        const variant = outcome.state === "READY" ? "ready" : outcome.state === "NO_CANDIDATES" ? "no-candidates" : "not-ready";
        container.append(element(doc, "p", outcome.message, "rec-outcome", { "data-state": variant, role: "status" }));
    }

    function labelled(doc, label, value) {
        const row = element(doc, "div", undefined, "rec-detail__row");
        row.append(element(doc, "span", label, "rec-detail__label"), element(doc, "span", value === null || value === undefined || value === "" ? "not reported" : value, "rec-detail__value"));
        return row;
    }

    function placementText(placement) {
        const rows = Object.entries(placement || {}).flatMap(([service, pods]) =>
            (pods || []).map((p) => `${service}: ${p.pod} on ${p.node}${p.ready === false ? " (not ready)" : ""}`));
        return rows.length ? rows.join("; ") : null;
    }

    /** For each candidate: what is wrong, what to change, whether it is feasible and why, the effect, the cost, the ranking. */
    function renderCandidateDetails(container, detail, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        if (!detail || !Array.isArray(detail.candidates) || detail.candidates.length === 0) {
            container.append(missingState(doc, "No candidate was generated", detail && detail.outcome ? detail.outcome.message : "The engine has no candidate for this workflow and failure."));
            return;
        }
        for (const candidate of detail.candidates) {
            const card = element(doc, "div", undefined, "card rec-detail", { "data-ready": String(candidate.approval.ready) });
            card.append(element(doc, "h4", `${candidate.service} · ${strategyName(candidate.type)}`, "rec-detail__title"));
            card.append(labelled(doc, "Root cause", candidate.rootCause));
            card.append(labelled(doc, "Recommended change", candidate.recommendedChange));
            const feasibility = candidate.feasibility || {};
            card.append(labelled(doc, "Feasibility", `${codeLabel(feasibility.status)}${feasibility.explanation ? ` — ${feasibility.explanation}` : ""}`));
            for (const finding of feasibility.findings || []) {
                card.append(labelled(doc, "  finding", `${finding.constraint}: ${finding.status} — ${finding.detail}`));
            }
            if ((feasibility.missingEvidence || []).length) card.append(labelled(doc, "Evidence missing", feasibility.missingEvidence.join("; ")));
            card.append(labelled(doc, "Current placement", placementText(candidate.currentPlacement)));
            card.append(labelled(doc, "Blast radius", `${percent(candidate.predicted.blastRadiusBefore)} now → ${percent(candidate.predicted.blastRadiusAfter)} predicted (${candidate.predicted.riskReduction} points)`));
            card.append(labelled(doc, "Cost", `${codeLabel(candidate.cost.resource)} — ${candidate.cost.note || "no note"}; ${candidate.cost.configuration}`));
            card.append(labelled(doc, "Ranking", candidate.selectionJustification));
            for (const note of candidate.notes || []) card.append(element(doc, "p", note, "rec-detail__note"));
            card.append(element(doc, "p",
                candidate.approval.ready
                    ? `Ready for approval${candidate.approval.requiresJustification ? " (needs a written justification)" : ""}. Approving does not apply anything.`
                    : `Not ready for approval: ${candidate.approval.reason || "no reason reported"}`,
                "rec-detail__approval", { "data-ready": String(candidate.approval.ready) }));
            container.append(card);
        }
    }

    // ---- every failure ---------------------------------------------------------------------------------------

    /** One row per worker node assessed as the failure, each with its own selected candidate. ``onOpen(node)`` shows that failure. */
    function renderFailures(container, overview, onOpen, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        container.append(element(doc, "p", overview.scope, "rec-failures__scope"));
        if (!overview.failures.length) {
            container.append(missingState(doc, "No worker nodes", "The topology has no worker node to assess."));
            return;
        }
        const table = element(doc, "table", undefined, "data-table");
        const head = element(doc, "tr");
        for (const title of ["Failed node", "Blast radius", "Single point of failure", "Candidates", "Selected for this failure", "Cost", ""]) {
            head.append(element(doc, "th", title, undefined, { scope: "col" }));
        }
        const body = element(doc, "tbody");
        for (const row of overview.failures) {
            const tr = element(doc, "tr", undefined, undefined, { "data-node": row.failedNode });
            const selected = row.selected;
            const open = element(doc, "button", "Show", "secondary-button", { type: "button", "data-action": "open-failure" });
            open.addEventListener("click", () => onOpen(row.failedNode));
            for (const value of [
                row.failedNode,
                percent(row.blastRadiusBefore),
                row.singlePointOfFailure ? "Yes" : "No",
                `${row.feasible} feasible · ${row.ranked} ranked of ${row.candidates}`,
                selected ? `${strategyName(selected.type)} · ${selected.service} (${percent(selected.predictedBlastRadius)} after)` : (row.singlePointOfFailure ? "None can be selected" : "Nothing to fix"),
                selected ? selected.resourceCost : MISSING,
            ]) {
                tr.append(element(doc, "td", value));
            }
            const action = element(doc, "td");
            action.append(open);
            tr.append(action);
            body.append(tr);
        }
        table.append(element(doc, "thead"), body);
        table.children[0].append(head);
        container.append(table);
    }

    // ---- the proposal under review ---------------------------------------------------------------------------

    /**
     * What is about to be approved, in one place, each fact marked with what kind of fact it is: a live observation, a user
     * declaration, a model assumption, a prediction, or missing evidence. Nothing is computed here; every value is the engine's.
     * ``clusterIdentity`` is the connected cluster's name when the page runs in the extension (empty otherwise).
     */
    function renderProposalSummary(container, detail, candidate, clusterIdentity, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        if (!detail || !candidate) return;
        const evidence = detail.evidence || {};
        const declared = evidence.source === "declared-workflow";
        const proposal = candidate.yamlProposal || {};
        const deployments = (proposal.deployments || []).map((d) => d.deployment);
        const declaration = (detail.declared && detail.declared.declaredBy) || null;
        const rows = [
            ["Connected cluster", clusterIdentity || null, "Live observation"],
            ["Application (namespace)", evidence.namespace, "Live observation"],
            ["Workflow", evidence.workflowId ? `${evidence.workflowId} — ${declared
                ? `declared${declaration ? ` by ${declaration}` : ""}, not observed or frozen` : `frozen by Component 1 (${evidence.workflowFreezeId || "freeze id not reported"})`}` : null,
                declared ? "User declaration" : "Live observation"],
            ["Assessed failed node", evidence.failedNode ? `${evidence.failedNode} (${evidence.failedNodeChosen === "requested" ? "chosen by you" : "the node whose loss hurts most"})` : null,
                "Model assumption"],
            ["Candidate strategy", `${strategyName(candidate.type)} for ${candidate.service}`, "Prediction"],
            ["Target Deployments", deployments.length ? deployments.join(", ") : null, "Live observation"],
            ["Proposed change", candidate.recommendedChange, "Prediction"],
            ["Expected benefit", candidate.predicted ? `blast radius ${percent(candidate.predicted.blastRadiusBefore)} → ${percent(candidate.predicted.blastRadiusAfter)} (${candidate.predicted.riskReduction} percentage points)` : null,
                "Prediction"],
            ["Resource cost", candidate.cost ? `${codeLabel(candidate.cost.resource)} — ${candidate.cost.configuration}` : null, "Model assumption"],
            ["Feasibility", candidate.feasibility ? codeLabel(candidate.feasibility.status) : null, "Live observation"],
        ];
        const list = element(doc, "dl", undefined, "rec-summary");
        for (const [term, value, kind] of rows) {
            list.append(element(doc, "dt", term));
            const description = element(doc, "dd");
            if (value === null || value === undefined || value === "") {
                description.append(element(doc, "span", "Missing evidence", "rec-summary__kind rec-summary__kind--missing"));
            } else {
                description.append(element(doc, "span", value), element(doc, "span", kind, "rec-summary__kind"));
            }
            list.append(description);
        }
        container.append(element(doc, "div", "Proposal under review", "op-subtitle"), list);
    }

    // ---- the failure being assessed, in the words a person reads it in ---------------------------------------------------
    // Component 3 predicts from topology: it reports which workflows and services stop working when a node is lost. It
    // measures no success rate, latency or error rate, so none is shown here. Every figure is the engine's own.

    function tile(doc, label, value, sub, valueClass) {
        const node = element(doc, "div", undefined, "rec-tile");
        node.append(element(doc, "div", label, "rec-tile__label"), element(doc, "div", value, `rec-tile__value${valueClass ? ` ${valueClass}` : ""}`));
        if (sub) node.append(element(doc, "div", sub, "rec-tile__sub"));
        return node;
    }

    /** The problem in one sentence, from the engine's own failure-impact result. Null when the engine reported none: nothing is guessed. */
    function problemStatement(detail) {
        const impact = detail && detail.baselineImpact;
        if (!impact || impact.blastRadius === null || impact.blastRadius === undefined) return null;
        const node = (detail.evidence && detail.evidence.failedNode) || "the assessed node";
        return `If ${node} fails, the workflow ${Number(impact.blastRadius) > 0 ? "does not survive" : "survives"}.`;
    }

    /** Step 2: what is lost if the assessed node fails. A statement of a prediction, never of an observation. */
    function renderImpactSummary(container, detail, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        const impact = detail && detail.baselineImpact;
        const evidence = (detail && detail.evidence) || {};
        if (!impact || impact.blastRadius === null || impact.blastRadius === undefined) {
            container.append(missingState(doc, "The failure impact was not reported",
                "The engine did not return Component 3's result for this failure, so no impact is shown. This page never estimates one."));
            return;
        }
        const node = evidence.failedNode || "the assessed node";
        const lost = Number(impact.blastRadius) > 0;
        const failedServices = impact.failedServices || [];
        const failedWorkflows = impact.failedWorkflows || [];
        const known = (detail.currentDeployment && detail.currentDeployment.services) || [];
        const roles = (detail.workflowServices || []);
        const total = roles.length || known.length;

        const banner = element(doc, "div", undefined, "rec-impact__banner", { "data-state": lost ? "lost" : "survives", role: "status" });
        banner.append(
            element(doc, "div", `If ${node} fails`, "rec-impact__lead"),
            element(doc, "div", lost ? "The workflow does not survive" : "The workflow survives", "rec-impact__headline"),
            element(doc, "div", "Predicted from the live topology, not measured.", "rec-impact__sub")
        );
        // How much of the workflow is lost, as the one bar, then the services that go with the node.
        const meter = element(doc, "div", undefined, "rec-impact__meter");
        meter.append(gauge(doc, null, impact.blastRadius, "Blast radius", { after: "Lost" }));
        const lostLine = element(doc, "div", undefined, "rec-impact__lost-line");
        if (failedServices.length) {
            lostLine.append(element(doc, "span", total ? `Services lost (${failedServices.length} of ${total})` : "Services lost", "rec-impact__lost-label"));
            const list = element(doc, "ul", undefined, "rec-impact__chips");
            for (const name of failedServices) list.append(element(doc, "li", name, "rec-impact__chip"));
            lostLine.append(list);
        } else {
            lostLine.append(element(doc, "span", "No service is lost when this node fails.", "rec-impact__lost-label"));
        }
        if (failedWorkflows.length) lostLine.append(element(doc, "p", `Workflow lost: ${failedWorkflows.join(", ")}`, "ui-note"));

        // The four figures are the research reading of the same result.
        const tiles = element(doc, "div", undefined, "rec-impact__tiles rec-research-only");
        tiles.append(
            tile(doc, "Workflow survival", percent(impact.survivability)),
            tile(doc, "Blast radius", percent(impact.blastRadius), "share of workflows lost"),
            tile(doc, "Services lost", total ? `${failedServices.length} of ${total}` : String(failedServices.length)),
            tile(doc, "Failed node", node, evidence.failedNodeChosen === "requested" ? "chosen by you" : "the node whose loss hurts most", "rec-tile__value--name")
        );
        container.append(banner, meter, lostLine, tiles);

        const services = element(doc, "details", undefined, "rec-impact__services");
        services.append(element(doc, "summary", roles.length ? "Services in this workflow" : "Where each service runs"));
        if (roles.length) services.append(servicesTable(doc, roles, failedServices, node));
        else services.append(element(doc, "p", "The frozen workflow did not report its services, so only the lost ones are listed above.", "ui-note"));
        container.append(services);
    }

    /** Each service of the workflow, the role Component 1's frozen graph gives it, and whether it is lost with the node. */
    function servicesTable(doc, roles, failedServices, node) {
        const lost = new Set(failedServices);
        const listed = new Set(roles.map((entry) => entry.name));
        const rows = [...roles, ...failedServices.filter((name) => !listed.has(name)).map((name) => ({ name, role: null }))];
        const table = element(doc, "table", undefined, "data-table rec-services");
        const head = element(doc, "tr");
        for (const title of ["Service", "Role in workflow", `If ${node} fails`]) head.append(element(doc, "th", title, undefined, { scope: "col" }));
        const header = element(doc, "thead");
        header.append(head);
        const body = element(doc, "tbody");
        for (const entry of rows) {
            const gone = lost.has(entry.name);
            const tr = element(doc, "tr", undefined, undefined, { "data-state": gone ? "lost" : "survives" });
            // codeLabel reads upper-case codes, so the role is upper-cased first.
            const role = entry.role ? codeLabel(String(entry.role).toUpperCase()) : "Not reported";
            tr.append(element(doc, "td", entry.name, "rec-services__name"), element(doc, "td", role));
            const status = element(doc, "td");
            status.append(element(doc, "span", gone ? "Lost" : "Survives", `status-badge status-badge--${gone ? "danger" : "success"}`));
            tr.append(status);
            body.append(tr);
        }
        table.append(header, body);
        const scroll = element(doc, "div", undefined, "table-scroll");
        scroll.append(table);
        return scroll;
    }

    /** Step 5: the one improvement being put to approval, with what it is expected to do. Every value is the engine's. */
    function renderSelectedImprovement(container, candidate, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        if (!candidate) {
            container.append(missingState(doc, "Nothing to approve", "The engine selected no candidate, so there is no improvement to put to approval."));
            return;
        }
        const heading = element(doc, "div", undefined, "rec-selected__heading");
        const name = element(doc, "div");
        name.append(element(doc, "div", strategyName(candidate.type), "rec-selected__title"), element(doc, "div", `on ${candidate.service}`, "candidate-card__target"));
        heading.append(
            element(doc, "span", undefined, "candidate-card__icon", { "data-kind": String(candidate.type || "").toLowerCase(), "aria-hidden": "true" }),
            name,
            chipOf(doc, costLabel(candidate.cost ? candidate.cost.resource : null), "muted", "rec-selected__cost")
        );
        container.append(heading);
        if (candidate.recommendedChange) container.append(element(doc, "p", candidate.recommendedChange, "rec-selected__change", { title: candidate.recommendedChange }));
        const predicted = candidate.predicted || {};
        container.append(gauge(doc, predicted.blastRadiusBefore, predicted.blastRadiusAfter), element(doc, "p", "Predicted, not observed.", "ui-note"));
    }

    // ---- where the person is in the flow ------------------------------------------------------------------------------

    const STEP_LABELS = ["Select", "Impact", "Compare", "Prove it", "Approve"];

    function captionFor(index, state, facts, proof) {
        if (state === "done") return "Done";
        if (state === "blocked") return "Needs attention";
        if (state === "recorded") return "Earlier approval";
        if (state === "current") {
            if (index === 0) return "Choose";
            if (index === 1) return "Reading…";
            if (index === 3) return proof === "running" ? "Running" : "Not run yet";
            if (index === 4) return facts.approval === "APPROVED" ? "Apply next" : "Ready";
            return "Next";
        }
        return index === 4 ? "After step 4" : "Waiting";
    }

    /**
     * The state of the five steps, from facts the page already has: ``chosen`` (an application and a workflow are chosen),
     * ``status`` (the controller's), ``selected`` (the engine selected a candidate), ``proof`` (none, running, passed, failed or
     * attention: what the test-cluster step last reported), ``proven`` (whether the SERVER says this very change was proven on a test
     * cluster; unknown until it has answered), ``approval`` (the stored decision: none, PENDING_APPROVAL, APPROVED or REJECTED) and
     * ``applied`` (a change was applied).
     *
     * A step is "done" only when its own evidence exists, and a later step waits for an earlier one. The approval is the exception that
     * proves the rule: it can be opened before the change is proven, but it is never shown as the step to do, or as done, until step 4
     * has passed. A decision recorded before that is shown for what it is, an earlier approval ("recorded"), not as validation.
     * Each step also says whether it can be opened (``available``).
     */
    function computeSteps(input) {
        const facts = input || {};
        const loading = facts.status === "loading" || facts.status === "loading-recommendation";
        const proof = facts.proof || "none";
        const proven = facts.proven === true || ((facts.proven === undefined || facts.proven === null) && proof === "passed");
        const states = ["waiting", "waiting", "waiting", "waiting", "waiting"];
        const available = [true, false, false, false, false];
        states[0] = facts.chosen ? "done" : "current";
        if (facts.chosen) {
            available[1] = true;
            states[1] = loading ? "current" : facts.status === "ready" ? "done" : "blocked";
            if (states[1] === "done") {
                available[2] = true;
                states[2] = facts.selected ? "done" : "blocked";
                if (states[2] === "done") {
                    available[3] = available[4] = true;
                    states[3] = proof === "running" ? "current" : proven ? "done" : proof === "failed" || proof === "attention" ? "blocked" : "current";
                    if (proven && proof !== "running") states[4] = facts.applied ? "done" : "current";
                    else states[4] = facts.approval && facts.approval !== "none" ? "recorded" : "waiting";
                }
            }
        }
        return states.map((state, index) => ({
            number: index + 1, label: STEP_LABELS[index], state, available: available[index], caption: captionFor(index, state, facts, proof),
        }));
    }

    /**
     * Five buttons. ``options.viewing`` is the step on screen (it is not the same as the step to do), ``options.onSelect(number)`` opens
     * one. A step that cannot be opened yet is disabled. State is a mark, a word and a colour, never the colour alone.
     */
    function renderStepper(container, steps, options) {
        const doc = documentOf(options);
        const viewing = options && options.viewing;
        container.replaceChildren(...steps.map((step) => {
            const item = element(doc, "li", undefined, "rec-stepper__item", { "data-state": step.state });
            if (step.number === viewing) item.setAttribute("data-viewing", "true");
            const button = element(doc, "button", undefined, "rec-stepper__btn", { type: "button", "data-step": String(step.number) });
            if (step.number === viewing) button.setAttribute("aria-current", "step");
            if (!step.available) button.disabled = true;
            const mark = step.state === "done" ? "✓" : step.state === "blocked" ? "!" : step.state === "recorded" ? "◷" : String(step.number);
            const text = element(doc, "span", undefined, "rec-stepper__text");
            text.append(element(doc, "span", step.label, "rec-stepper__label"), element(doc, "span", step.caption, "rec-stepper__caption"));
            button.append(element(doc, "span", mark, "rec-stepper__mark", { "aria-hidden": "true" }), text);
            if (options && options.onSelect) button.addEventListener("click", () => options.onSelect(step.number));
            item.append(button);
            return item;
        }));
    }

    /**
     * What the person has to do next in step 1 while the page waits for a choice, or null when nothing is waiting. ``ctx`` is the
     * controller's view. A declared workflow is never chosen for the person, so when only declared workflows apply the hint says
     * so and leaves the choice to them.
     */
    function nextActionHint(ctx) {
        if (!ctx || (ctx.status !== "needs-input" && ctx.status !== "blocked")) return null;
        if (ctx.status === "needs-input" && (ctx.namespaceChoice === "ambiguous" || !ctx.namespace)) {
            return { field: "namespace", text: "Choose the application to analyse." };
        }
        if (ctx.workflowId) return null;                      // a workflow is already chosen: what is missing is evidence, not a choice
        // "blocked" with a workflow to choose is the case where only a declared workflow exists: the backend offers it but never picks it.
        const usable = (ctx.workflows || []).filter((workflow) => workflow.applicable);
        if (!usable.length) return null;
        if (usable.every((workflow) => workflow.declared)) {
            return { field: "workflow", text: "Choose a workflow to continue. Declared workflows are for research and are never picked for you." };
        }
        return { field: "workflow", text: "Choose a workflow to continue." };
    }

    /** One line over the evidence checklist, and whether something in it needs the person's attention. */
    function evidenceSummary(items) {
        const list = items || [];
        const available = list.filter((item) => item.state === "available").length;
        const attention = list.some((item) => item.state === "missing" || item.state === "incomplete");
        return { text: list.length ? `Evidence: ${available} of ${list.length} available` : "Evidence", attention };
    }

    window.ResilienceRecommendations = {
        renderSelectedImprovement,
        renderImpactSummary,
        computeSteps,
        problemStatement,
        renderStepper,
        gauge,
        strategyName,
        costLabel,
        evidenceSummary,
        nextActionHint,
        renderProposalSummary,
        renderFailures,
        rankCandidates,
        selectedCandidate,
        summarise,
        renderCandidateCards,
        renderRankingTable,
        renderValidation,
        renderYaml,
        renderPlacement,
        renderEvidenceChecklist,
        renderOutcome,
        renderCandidateDetails,
    };
})();
