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

    function stat(doc, label, value, title, className) {
        const wrapper = element(doc, "div", undefined, className);
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

            const titleBlock = element(doc, "div", undefined, "candidate-card__heading");
            const heading = element(doc, "div");
            heading.append(
                element(doc, "div", codeLabel(candidate.type), "candidate-card__title"),
                element(doc, "div", `Target: ${candidate.service}`, "candidate-card__target")
            );
            titleBlock.append(
                element(doc, "span", undefined, "candidate-card__icon", { "data-kind": String(candidate.type || "").toLowerCase(), "aria-hidden": "true" }),
                heading
            );
            const badges = element(doc, "div", undefined, "stack-sm");
            if (rank !== null) badges.append(element(doc, "span", `#${rank}`, "status-badge status-badge--muted"));
            badges.append(
                element(doc, "span", candidate.feasible ? "Feasible" : "Not Feasible",
                    `status-badge status-badge--${candidate.feasible ? "success" : "danger"}`)
            );
            if (selected) badges.append(element(doc, "span", "Selected Recommendation", "status-badge status-badge--info"));
            if (selected) badges.append(element(doc, "span", "✓", "candidate-card__check", { "aria-hidden": "true" }));
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
            // The cost stays in the plain view; confidence and score are the ranking's own terms and belong to the research view.
            const secondary = element(doc, "div", undefined, "candidate-card__stats");
            secondary.append(
                stat(doc, "Resource Cost", candidate.resourceCost),
                stat(doc, "Confidence", ratioPercent(candidate.confidence), undefined, "rec-research-only"),
                stat(doc, "Score", ratioPercent(candidate.score), undefined, "rec-research-only")
            );

            card.append(header);
            const description = options && options.descriptions && options.descriptions[candidate.id];
            if (description) card.append(element(doc, "p", description, "candidate-card__description", { title: description }));
            card.append(primary, secondary);
            // Why a candidate cannot be put forward, in the card itself (the engine's own reason, never worded here).
            const reason = options && options.reasons && options.reasons[candidate.id];
            if (reason) card.append(element(doc, "p", reason, "candidate-card__reason", { title: reason }));
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
                selected ? `${codeLabel(selected.type)} · ${selected.service} (${percent(selected.predictedBlastRadius)} after)` : (row.singlePointOfFailure ? "None can be selected" : "Nothing to fix"),
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
            ["Candidate strategy", `${codeLabel(candidate.type)} for ${candidate.service}`, "Prediction"],
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
        const banner = element(doc, "div", undefined, "rec-impact__banner", { "data-state": lost ? "lost" : "survives", role: "status" });
        banner.append(
            element(doc, "div", `If ${node} fails`, "rec-impact__lead"),
            element(doc, "div", lost ? "The workflow does not survive" : "The workflow survives", "rec-impact__headline"),
            element(doc, "div", "Predicted from the live topology, not measured.", "rec-impact__sub")
        );
        const total = roles.length || known.length;
        const tiles = element(doc, "div", undefined, "rec-impact__tiles");
        tiles.append(
            tile(doc, "Workflow survival", percent(impact.survivability)),
            tile(doc, "Blast radius", percent(impact.blastRadius), "share of workflows lost"),
            tile(doc, "Services lost", total ? `${failedServices.length} of ${total}` : String(failedServices.length)),
            tile(doc, "Failed node", node, evidence.failedNodeChosen === "requested" ? "chosen by you" : "the node whose loss hurts most", "rec-tile__value--name")
        );
        const row = element(doc, "div", undefined, "rec-impact__row");
        row.append(banner, tiles);

        const lines = element(doc, "div", undefined, "rec-impact__lost");
        if (roles.length) {
            lines.append(element(doc, "div", "Services in this workflow", "op-subtitle"), servicesTable(doc, roles, failedServices, node));
        } else {
            lines.append(element(doc, "div", "Services lost", "op-subtitle"));
            if (failedServices.length) {
                const list = element(doc, "ul", undefined, "rec-impact__chips");
                for (const name of failedServices) list.append(element(doc, "li", name, "rec-impact__chip"));
                lines.append(list);
            } else {
                lines.append(element(doc, "p", "No service is lost when this node fails.", "ui-note"));
            }
        }
        if (failedWorkflows.length) lines.append(element(doc, "p", `Workflow lost: ${failedWorkflows.join(", ")}`, "ui-note"));
        container.append(row, lines);
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

    /** Step 4: the one improvement being put to approval, with what it is expected to do. Every value is the engine's. */
    function renderSelectedImprovement(container, candidate, options) {
        const doc = documentOf(options);
        container.replaceChildren();
        if (!candidate) {
            container.append(missingState(doc, "Nothing to approve", "The engine selected no candidate, so there is no improvement to put to approval."));
            return;
        }
        const heading = element(doc, "div", undefined, "rec-selected__heading");
        heading.append(
            element(doc, "span", undefined, "candidate-card__icon", { "data-kind": String(candidate.type || "").toLowerCase(), "aria-hidden": "true" }),
            element(doc, "div", `${codeLabel(candidate.type)} for ${candidate.service}`, "rec-selected__title")
        );
        container.append(heading);
        if (candidate.recommendedChange) container.append(element(doc, "p", candidate.recommendedChange, "rec-selected__change", { title: candidate.recommendedChange }));
        const predicted = candidate.predicted || {};
        const outcome = element(doc, "div", undefined, "rec-selected__outcome");
        outcome.append(
            stat(doc, "Blast radius now", percent(predicted.blastRadiusBefore)),
            stat(doc, "Predicted after", percent(predicted.blastRadiusAfter)),
            stat(doc, "Resource cost", candidate.cost ? codeLabel(candidate.cost.resource) : MISSING)
        );
        container.append(outcome, element(doc, "p", "Predicted, not observed.", "ui-note"));
    }

    // ---- where the person is in the flow ------------------------------------------------------------------------------

    const STEP_LABELS = ["Select application and workflow", "Analyse failure impact", "Compare candidate improvements", "Prove it on a test cluster", "Review and approve proposal"];
    const STEP_STATE_TEXT = { done: "Done", current: "Now", blocked: "Needs attention", todo: "Not yet" };

    /**
     * The state of the five steps, from facts the page already has: ``chosen`` (an application and a workflow are chosen),
     * ``status`` (the controller's), ``selected`` (the engine selected a candidate), ``proof`` (none, running, passed, failed or
     * attention: what the test-cluster step last reported) and ``applied`` (a change was applied).
     * A step is never shown as done unless its own evidence exists, and a later step waits for an earlier one. Proving is optional:
     * it never holds back the approval, so while nothing has been run it is "Not yet" and the approval is "Now".
     */
    function computeSteps(input) {
        const facts = input || {};
        const loading = facts.status === "loading" || facts.status === "loading-recommendation";
        const states = ["todo", "todo", "todo", "todo", "todo"];
        states[0] = facts.chosen ? "done" : "current";
        if (facts.chosen) {
            states[1] = loading ? "current" : facts.status === "ready" ? "done" : "blocked";
            if (states[1] === "done") {
                states[2] = facts.selected ? "done" : "blocked";
                if (states[2] === "done") {
                    const proof = facts.proof || "none";
                    states[3] = proof === "passed" ? "done" : proof === "running" ? "current" : proof === "failed" || proof === "attention" ? "blocked" : "todo";
                    states[4] = facts.applied ? "done" : proof === "running" ? "todo" : "current";
                }
            }
        }
        return states.map((state, index) => ({ number: index + 1, label: STEP_LABELS[index], state }));
    }

    function renderStepper(container, steps, options) {
        const doc = documentOf(options);
        container.replaceChildren(...steps.map((step) => {
            const item = element(doc, "li", undefined, "rec-stepper__item", { "data-state": step.state });
            if (step.state === "current") item.setAttribute("aria-current", "step");
            const mark = step.state === "done" ? "✓" : step.state === "blocked" ? "!" : String(step.number);
            item.append(
                element(doc, "span", mark, "rec-stepper__mark", { "aria-hidden": "true" }),
                element(doc, "span", step.label, "rec-stepper__label"),
                element(doc, "span", STEP_STATE_TEXT[step.state], "visually-hidden")
            );
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
        renderStepper,
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
