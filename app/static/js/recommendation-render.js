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

    // ---- candidate cards -------------------------------------------------------------------------------------

    function stat(doc, label, value, title) {
        const wrapper = element(doc, "div");
        wrapper.append(
            element(doc, "div", label, "candidate-card__stat-label"),
            element(doc, "div", value, "candidate-card__stat-value", title ? { title } : undefined)
        );
        return wrapper;
    }

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
            const card = element(doc, "div", undefined, `candidate-card${selected ? " candidate-card--selected" : ""}`);

            const titleBlock = element(doc, "div");
            titleBlock.append(
                element(doc, "div", codeLabel(candidate.type), "candidate-card__title"),
                element(doc, "div", `Target: ${candidate.service}`, "candidate-card__target")
            );
            const badges = element(doc, "div", undefined, "stack-sm");
            if (rank !== null) badges.append(element(doc, "span", `#${rank}`, "status-badge status-badge--muted"));
            badges.append(
                element(doc, "span", candidate.feasible ? "Feasible" : "Not Feasible",
                    `status-badge status-badge--${candidate.feasible ? "success" : "danger"}`)
            );
            if (selected) badges.append(element(doc, "span", "Selected Recommendation", "status-badge status-badge--info"));
            const header = element(doc, "div", undefined, "candidate-card__header");
            header.append(titleBlock, badges);

            const primary = element(doc, "div", undefined, "candidate-card__stats");
            primary.append(
                hasBaseline
                    ? stat(doc, "Before", percent(baseline))
                    : stat(doc, "Before", MISSING, "Baseline blast radius was not supplied with this recommendation"),
                stat(doc, "Predicted After", percent(candidate.predictedBlastRadius)),
                stat(doc, "Risk Reduction", `${candidate.riskReduction} pp`)
            );
            const secondary = element(doc, "div", undefined, "candidate-card__stats");
            secondary.append(
                stat(doc, "Resource Cost", candidate.resourceCost),
                stat(doc, "Confidence", ratioPercent(candidate.confidence)),
                stat(doc, "Score", ratioPercent(candidate.score))
            );

            card.append(header, primary, secondary);
            if (!hasBaseline) {
                card.append(element(doc, "p", "Baseline not provided: the blast radius before the change is shown only when a failure-impact result accompanies the recommendation.", "ui-note"));
            }
            return card;
        });
        container.replaceChildren(...cards);
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
                const label = `${codeLabel(candidate.type)} · ${candidate.service}${selected ? " (selected)" : ""}`;
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
            card.append(element(doc, "h4", `${candidate.service} · ${codeLabel(candidate.type)}`, "rec-detail__title"));
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

    window.ResilienceRecommendations = {
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
