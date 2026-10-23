/*
 * The applied-change panel of the Recommendations page. Owned by: Member 4 (L.A. Nemal).
 *
 * One persisted operation from Apply to Reset, drawn from what the server returns and from nothing else. The server decides the
 * stage (Review, Approved, Applying, Rollout, Analysis, Results), the steps beneath it, the headline and the next action
 * (application/operation.py), and it holds the baseline, the prediction and the post-change recalculation. This script draws
 * them, polls for them, and sends the person's explicit Apply, Reset and Retry. It calculates no research value: percentages are
 * printed as given, an unknown stays "unknown", and no progress percentage exists to show, so waiting is drawn as indeterminate.
 *
 * The panel lives OUTSIDE the page's re-rendered region. It is keyed by the application id the server stored, not by the selected
 * recommendation, so it survives a different selection, a recommendation that is no longer generated, and a page reload (it asks
 * the server for the current application at start). Reloading never repeats an Apply: nothing here writes except on a click.
 *
 * Requests are sequenced. Every response is tagged with the order it was issued in and the epoch of the person's last action; an
 * older response than the one already shown, or one from before the person acted or the cluster changed, is dropped. After a
 * failed Apply or Reset the status is read again and the controls stay off until it has been: a timeout does not prove the
 * cluster rejected the change.
 *
 * All text goes through textContent. Handlers are attached to the elements cloned from the template, once, and never again.
 */
(function () {
    "use strict";

    const BASE = "/api/recommendations";
    const RESEARCH_BASE = "/api/recommendations/declared";
    const STAGES = ["review", "approved", "applying", "rollout", "analysis", "results"];
    const STAGE_LABEL = { review: "Review", approved: "Approved", applying: "Applying", rollout: "Rollout", analysis: "Analysis", results: "Results" };
    const RESET_STAGE_LABEL = { ...STAGE_LABEL, applying: "Resetting" };
    const TONE_TEXT = { busy: "In progress", ok: "Done", attention: "Needs attention", neutral: "Note" };
    const TONE_VARIANT = { busy: "warning", ok: "success", attention: "danger", neutral: "muted" };
    const STEP_TEXT = { done: "done", active: "in progress", attention: "needs attention", pending: "waiting" };
    const FAST_POLL_MS = 1500;            // while a request is in flight: shows the steps the server has recorded so far
    const MAX_AUTO_POLL_MS = 15 * 60 * 1000;
    const MAX_BACKOFF = 8;
    const UNKNOWN = "unknown";

    const REVIEW_NOTE = {
        apply: "Applying changes the connected cluster. It patches only the Deployments in the approved YAML (more replicas, a hostname " +
            "spread, required anti-affinity), stores the state it replaces first, and rolls their pods out again. Reset puts that stored " +
            "state back.",
        reset: "Reset puts back the replica count and placement constraints stored when this was applied. Other changes made to those " +
            "Deployments since are kept.",
    };

    function percent(value) {
        return value === null || value === undefined || Number.isNaN(Number(value)) ? UNKNOWN : `${Number(value)}%`;
    }

    function points(value) {
        return value === null || value === undefined || Number.isNaN(Number(value)) ? UNKNOWN : `${Number(value)} percentage points`;
    }

    function formatTime(iso) {
        if (!iso) return UNKNOWN;
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? String(iso) : date.toLocaleString();
    }

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function chip(doc, text, variant) {
        return make(doc, "span", text, `status-badge status-badge--${variant || "muted"} status-badge--no-dot`);
    }

    function errorMessage(result, fallback) {
        const detail = result && result.payload && result.payload.detail;
        if (detail && typeof detail === "object") return detail.message || fallback;
        return typeof detail === "string" && result.status < 500 ? detail : fallback;
    }

    function applicationUrl(status, suffix) {
        const base = status.tier === "research" ? RESEARCH_BASE : BASE;
        return `${base}/${encodeURIComponent(status.recommendationId)}${suffix}`;
    }

    // ---- drawing -------------------------------------------------------------------------------------------------------

    /** Which analysis the card row shows: the one of the latest write (apply, or reset after a reset). */
    function currentAnalysis(status) {
        const direction = status && status.operation && status.operation.direction;
        return direction && status.analysis ? status.analysis[direction] || null : null;
    }

    function drawStages(doc, list, stage, direction) {
        const labels = direction === "reset" ? RESET_STAGE_LABEL : STAGE_LABEL;
        const at = STAGES.indexOf(stage);
        list.replaceChildren(...STAGES.map((name, index) => {
            const state = index < at ? "done" : index === at ? "current" : "upcoming";
            const item = make(doc, "li", undefined, `op-stage op-stage--${state}`, state === "current" ? { "aria-current": "step" } : {});
            item.append(make(doc, "span", labels[name], "op-stage__name"),
                make(doc, "span", state === "done" ? " (done)" : state === "current" ? " (current)" : " (not yet)", "visually-hidden"));
            return item;
        }));
    }

    function drawSteps(doc, list, steps) {
        list.replaceChildren(...steps.map((step) => {
            const item = make(doc, "li", undefined, `op-step op-step--${step.state}`);
            item.append(make(doc, "span", step.label, "op-step__label"), make(doc, "span", ` — ${STEP_TEXT[step.state] || step.state}`, "op-step__state"));
            if (step.at) item.append(make(doc, "span", ` · ${formatTime(step.at)}`, "op-step__time text-muted"));
            return item;
        }));
    }

    function failedLine(impact) {
        if (!impact || impact.failedServices === undefined || impact.failedServices === null) return "failed services: unknown";
        return impact.failedServices.length ? `failed services: ${impact.failedServices.join(", ")}` : "no service fails";
    }

    function drawCard(doc, title, kind, impact, evidenceTime, missing) {
        const card = make(doc, "div", undefined, "op-card", { "data-card": title.toLowerCase() });
        card.append(make(doc, "div", title, "op-card__title"), chip(doc, kind, impact ? "info" : "muted"));
        if (!impact) {
            card.append(make(doc, "div", missing || "Missing evidence", "op-card__missing"));
            return card;
        }
        card.append(make(doc, "div", percent(impact.blastRadius), "op-card__value"),
            make(doc, "div", "blast radius", "op-card__sub text-muted"),
            make(doc, "div", failedLine(impact), "op-card__sub"));
        if (evidenceTime) card.append(make(doc, "div", `evidence ${formatTime(evidenceTime)}`, "op-card__sub text-muted"));
        return card;
    }

    function drawCards(doc, container, status, analysis) {
        const reset = status.operation.direction === "reset";
        const afterMissing = !analysis ? "No analysis was recorded for this change."
            : analysis.status === "COMPLETED" ? null
            : analysis.status === "PENDING" ? "Not available until the rollout has completed and the topology has been read."
            : (analysis.error && analysis.error.message) || `Analysis ${analysis.status.toLowerCase()}.`;
        container.replaceChildren(
            drawCard(doc, "Before", "Prediction from live topology", analysis && analysis.baseline, analysis && analysis.baseline && analysis.baseline.topologyCapturedAt,
                "Missing evidence: no baseline was stored."),
            drawCard(doc, reset ? "Restoration target" : "Predicted", reset ? "The pre-change baseline" : "Prediction (virtual simulation)",
                analysis && analysis.predicted, null, "Missing evidence: no prediction was stored."),
            drawCard(doc, reset ? "After reset" : "After", "Recalculated from live observation", afterMissing ? null : analysis.observed,
                analysis && analysis.observed && analysis.observed.topologyCapturedAt, afterMissing));
    }

    function barRow(doc, label, value) {
        const row = make(doc, "div", undefined, "op-bar-row");
        row.append(make(doc, "span", label, "op-bar-row__label"));
        const track = make(doc, "span", undefined, "op-bar-row__track", { "aria-hidden": "true" });
        if (value !== null && value !== undefined) {
            const fill = make(doc, "span", undefined, "op-bar-row__fill");
            fill.style.width = `${Math.max(0, Math.min(100, Number(value)))}%`;
            track.append(fill);
        }
        row.append(track, make(doc, "span", percent(value), "op-bar-row__value"));
        return row;
    }

    function drawBars(doc, container, analysis, reset) {
        if (!analysis) return container.replaceChildren();
        const before = analysis.baseline && analysis.baseline.blastRadius;
        const predicted = analysis.predicted && analysis.predicted.blastRadius;
        const after = analysis.observed && analysis.observed.blastRadius;
        const group = make(doc, "div", undefined, "op-bars", { role: "img",
            "aria-label": `Blast radius: before ${percent(before)}, ${reset ? "restoration target" : "predicted"} ${percent(predicted)}, after ${percent(after)}` });
        group.append(barRow(doc, "Before", before), barRow(doc, reset ? "Target" : "Predicted", predicted), barRow(doc, "After", after));
        container.replaceChildren(group);
    }

    function drawReduction(doc, container, analysis, reset) {
        const comparison = analysis && analysis.comparison;
        if (!comparison) return container.replaceChildren();
        const lines = [];
        if (comparison.reductionPoints === null || comparison.reductionPoints === undefined) {
            lines.push("Reduction: not available, because a blast radius is unknown.");
        } else {
            const word = { improved: "improved", worsened: "worsened", unchanged: "unchanged" }[comparison.direction] || UNKNOWN;
            lines.push(`${reset ? "Change since before the change" : "Reduction"}: ${points(comparison.reductionPoints)} (risk ${word}).`);
            lines.push(comparison.relativeReductionPercent === null || comparison.relativeReductionPercent === undefined
                ? "Relative reduction (a separate measure): not defined, the baseline is 0%."
                : `Relative reduction (a separate measure): ${comparison.relativeReductionPercent}%.`);
        }
        if (!reset && comparison.predictionDeltaPoints !== null && comparison.predictionDeltaPoints !== undefined) {
            lines.push(`Recalculated risk against the proposal's prediction: ${comparison.predictionDeltaPoints === 0 ? "the same" : points(comparison.predictionDeltaPoints) + " " + (comparison.predictionDeltaPoints > 0 ? "above" : "below") + " the prediction"}.`);
        }
        if (reset && comparison.restoration) {
            lines.push(comparison.restoration.verified ? "The Deployments hold the stored replica count and placement constraints again."
                : `The Deployments differ from the stored state: ${(comparison.restoration.differences || []).join("; ")}.`);
        }
        container.replaceChildren(...lines.map((line) => make(doc, "p", line, "op-reduction__line")));
    }

    function podCell(doc, summary) {
        const cell = make(doc, "td");
        if (!summary) {
            cell.append(chip(doc, "Missing evidence", "muted"));
            return cell;
        }
        cell.append(make(doc, "div", `replicas ${summary.declaredReplicas === null || summary.declaredReplicas === undefined ? UNKNOWN : summary.declaredReplicas}`
            + ` · ready ${summary.readyReplicas === null || summary.readyReplicas === undefined ? UNKNOWN : summary.readyReplicas}`));
        const nodes = Object.entries(summary.byNode || {}).map(([node, count]) => `${node} ×${count}`).join(", ");
        cell.append(make(doc, "div", nodes || "no pods placed", "text-muted"));
        return cell;
    }

    function drawPlacement(doc, container, analysis, reset) {
        const services = analysis ? Array.from(new Set([
            ...Object.keys((analysis.baseline && analysis.baseline.placement) || {}),
            ...Object.keys((analysis.predicted && analysis.predicted.placement) || {}),
            ...Object.keys((analysis.observed && analysis.observed.placement) || {}),
        ])) : [];
        if (!services.length) return container.replaceChildren();
        const table = make(doc, "table", undefined, "data-table");
        const head = make(doc, "tr");
        ["Service", "Before (live topology at approval)", reset ? "Restoration target" : "Predicted (simulation)", "After (live topology now)"]
            .forEach((title) => head.append(make(doc, "th", title, undefined, { scope: "col" })));
        const thead = make(doc, "thead");
        thead.append(head);
        table.append(thead);
        const body = make(doc, "tbody");
        services.forEach((name) => {
            const row = make(doc, "tr");
            row.append(make(doc, "th", name, undefined, { scope: "row" }),
                podCell(doc, analysis.baseline && analysis.baseline.placement && analysis.baseline.placement[name]),
                podCell(doc, analysis.predicted && analysis.predicted.placement && analysis.predicted.placement[name]),
                podCell(doc, analysis.observed && analysis.observed.placement && analysis.observed.placement[name]));
            body.append(row);
        });
        table.append(body);
        container.replaceChildren(make(doc, "div", "Pod placement of the changed services", "op-subtitle"), table);
    }

    function definition(doc, list, term, value) {
        list.append(make(doc, "dt", term), make(doc, "dd", value === null || value === undefined || value === "" ? UNKNOWN : value));
    }

    function drawTechnical(doc, slots, status, analysis, raw) {
        const application = status.application;
        const list = make(doc, "dl", undefined, "op-facts");
        const basis = (analysis && analysis.basis) || {};
        definition(doc, list, "Application ID", application.applicationId);
        definition(doc, list, "Recommendation / proposal", `${application.recommendationId} (approval ${application.approvalId})`);
        definition(doc, list, "Tier", application.tier === "research" ? "Research approval (declared workflow)" : "Production approval (frozen workflow)");
        definition(doc, list, "Cluster / context", `${(application.cluster || {}).context || UNKNOWN} · ${(application.cluster || {}).apiServer || UNKNOWN}`);
        definition(doc, list, "Cluster identity (kube-system UID)", (application.cluster || {}).clusterUid);
        definition(doc, list, "Namespace", application.namespace);
        definition(doc, list, "Workflow", basis.workflowId);
        definition(doc, list, "Workflow source", basis.workflowBasis === "DECLARED"
            ? `User declaration by ${basis.declaredBy || UNKNOWN} (digest ${basis.declarationDigest || UNKNOWN}), not observed or frozen`
            : `Frozen by Component 1 (freeze ${basis.workflowFreezeId || UNKNOWN})`);
        definition(doc, list, "Assessed failed node", basis.failedNode ? `${basis.failedNode} (model assumption: ${basis.failedNodeChosen || UNKNOWN})` : null);
        definition(doc, list, "Candidate strategy", basis.strategy);
        definition(doc, list, "Target services", (basis.targetServices || []).join(", "));
        definition(doc, list, "Target Deployments", (application.deployments || []).join(", "));
        definition(doc, list, "YAML hash (sha256)", application.yamlSha256);
        definition(doc, list, "Applied", `${formatTime(application.appliedAt)} by ${application.appliedBy}`);
        if (application.resetAt) definition(doc, list, "Reset", `${formatTime(application.resetAt)} by ${application.resetBy}`);
        if (status.rollout) {
            definition(doc, list, "Rollout", `${status.rollout.direction}: ${status.rollout.state} (since ${formatTime(status.rollout.since)}, last read ${formatTime(status.rollout.checkedAt)}, deadline ${formatTime(status.rollout.deadline)})`);
        }
        if (analysis && analysis.observed) {
            definition(doc, list, "Post-change topology captured", `${formatTime(analysis.observed.topologyCapturedAt)} (after the rollout completed: ${analysis.observed.capturedAfterRollout === true ? "yes" : analysis.observed.capturedAfterRollout === false ? "no" : UNKNOWN})`);
            definition(doc, list, "Recalculated", formatTime(analysis.observed.recalculatedAt));
        }
        slots.facts.replaceChildren(list);

        const rows = ((status.rollout && status.rollout.deployments) || []).map((d) => {
            const row = make(doc, "tr");
            row.append(make(doc, "th", d.name, undefined, { scope: "row" }), make(doc, "td", `${d.observedGeneration ?? UNKNOWN} of ${d.generation ?? UNKNOWN}`),
                make(doc, "td", String(d.desired ?? UNKNOWN)), make(doc, "td", String(d.updated ?? UNKNOWN)), make(doc, "td", String(d.total ?? UNKNOWN)),
                make(doc, "td", String(d.ready ?? UNKNOWN)), make(doc, "td", String(d.available ?? UNKNOWN)), make(doc, "td", d.reason || d.state));
            return row;
        });
        if (rows.length) {
            const table = make(doc, "table", undefined, "data-table");
            const head = make(doc, "tr");
            ["Deployment", "Generation seen", "Desired", "Updated", "Total", "Ready", "Available", "State"].forEach((t) => head.append(make(doc, "th", t, undefined, { scope: "col" })));
            const thead = make(doc, "thead");
            thead.append(head);
            table.append(thead);
            const body = make(doc, "tbody");
            body.append(...rows);
            table.append(body);
            slots.rollout.replaceChildren(make(doc, "div", "Rollout evidence (read from the Deployments, not from the patch)", "op-subtitle"), table);
        } else {
            slots.rollout.replaceChildren(make(doc, "p", "No rollout evidence was recorded for this change.", "text-muted"));
        }

        slots.events.replaceChildren(...(application.events || []).map((event) => {
            const item = make(doc, "li");
            item.textContent = `${formatTime(event.at)} · ${event.event}${event.detail ? " — " + event.detail : ""}`;
            return item;
        }));

        const limits = [];
        if (analysis && analysis.error) limits.push(`Unavailable evidence: ${analysis.error.message}`);
        if (basis.workflowBasis === "DECLARED") limits.push("The workflow was declared by a person; every number derived from it is conditional on that declaration.");
        limits.push("The blast radius is a model's output over observed pod placement; no failure was injected, so nothing here is an experimental observation.");
        limits.push("Rollout cost (pods restarted while the change rolls out) is not modelled.");
        slots.limits.replaceChildren(...limits.map((line) => make(doc, "li", line)));

        const experiments = (status.experiments && status.experiments.measurements) || [];
        const experimentLines = !status.experiments || !status.experiments.available
            ? ["Controlled experiments: the experiment store could not be read, so whether any were run is unknown."]
            : experiments.length
                ? experiments.map((e) => `Controlled experiment ${e.experimentId}: ${e.status}, validated ${e.validated === null || e.validated === undefined ? UNKNOWN : e.validated}, prediction error ${e.predictionError === null || e.predictionError === undefined ? UNKNOWN : points(e.predictionError)} (${e.workflowBasis === "DECLARED" ? "conditional on a declared workflow" : "frozen workflow"}).`)
                : ["Controlled experiments: none have been run for this recommendation. The recalculation above is not an experiment."];
        slots.experiments.replaceChildren(...experimentLines.map((line) => make(doc, "li", line)));

        slots.yaml.textContent = application.yaml || "";
        if (raw) slots.raw.textContent = JSON.stringify(status, null, 2);
    }

    // ---- the controller ------------------------------------------------------------------------------------------------

    /**
     * @param {object} options
     * @param {Element} options.root          the panel's section (outside the re-rendered page region)
     * @param {Element} options.template      the <template> holding the panel's skeleton
     * @param {Document} options.document
     * @param {Function} options.fetchImpl    fetch
     * @param {Function} [options.schedule]   setTimeout
     * @param {Function} [options.cancel]     clearTimeout
     * @param {Function} [options.now]        Date.now
     * @param {Function} [options.onStatus]   called with each status the panel adopts
     */
    function createOperationPanel(options) {
        const { root, template, fetchImpl } = options;
        const doc = options.document;
        const schedule = options.schedule || ((fn, ms) => setTimeout(fn, ms));
        const cancel = options.cancel || ((id) => clearTimeout(id));
        const now = options.now || (() => Date.now());
        const onStatus = options.onStatus || (() => {});

        let slots = null;
        let status = null;              // the last status adopted from the server
        let pending = null;             // { kind: "apply" | "reset" | "retry", reconciling: bool }
        let message = null;             // { text, alert }
        let confirming = null;          // "reset" while the person is being asked once more
        let name = "";                  // the person's name, kept in memory only
        let timer = null;
        let inFlight = false;           // one status poll at a time
        let issued = 0;                 // order requests were sent in
        let shown = 0;                  // the order of the response currently shown
        let epoch = 0;                  // bumped by the person's actions and by a cluster change
        let backoff = 1;
        let watchingSince = null;
        let destroyed = false;

        // ---- mounting ----

        function mount() {
            if (slots) return;
            root.replaceChildren(template.content.cloneNode(true));
            const find = (role) => root.querySelector(`[data-role="${role}"]`);
            slots = {};
            ["op-badge", "op-stages", "op-headline", "op-progress", "op-message", "op-steps", "op-cards", "op-bars", "op-reduction", "op-placement",
                "op-label", "op-experiment", "op-name", "op-actions", "op-reset", "op-retry", "op-recheck", "op-confirm", "op-confirm-text",
                "op-confirm-yes", "op-confirm-no", "op-details", "op-facts", "op-rollout", "op-events", "op-limits", "op-experiments", "op-yaml", "op-raw",
                "op-name-wrap"].forEach((role) => { slots[role.replace("op-", "")] = find(role); });
            // Handlers go on the elements that were just cloned into the page, once.
            slots.reset.addEventListener("click", () => {
                confirming = "reset";
                draw();
                if (typeof slots.name.focus === "function") slots.name.focus();
            });
            slots["confirm-no"].addEventListener("click", () => { confirming = null; draw(); });
            slots["confirm-yes"].addEventListener("click", () => doReset());
            slots.retry.addEventListener("click", () => doRetry());
            slots.recheck.addEventListener("click", () => recheck());
            slots.name.addEventListener("input", () => { name = slots.name.value; });
            slots.details.addEventListener("toggle", () => { if (slots.details.open && status && status.application) draw(); });
        }

        // ---- drawing the current state ----

        function draw() {
            const visible = !!(status && status.application) || !!pending;
            root.hidden = !visible;
            if (!visible) return;
            mount();
            // A re-apply after a Reset: until the server records the new application, the one it still reports is the old one, and its
            // steps and numbers must not be drawn as if they belonged to this request.
            const stale = !!(pending && pending.kind === "apply" && status && status.application && status.application.applicationId === pending.previousId);
            const current = stale ? null : status;
            const operation = current && current.operation;
            const application = current && current.application;
            const busy = !!pending;
            const direction = pending && pending.kind === "reset" ? "reset" : (operation && operation.direction) || "apply";
            const stage = pending ? (pending.kind === "retry" ? "analysis" : "applying") : operation.stage;
            const tone = busy ? "busy" : operation.tone;

            slots.badge.textContent = TONE_TEXT[tone];
            slots.badge.className = `status-badge status-badge--${TONE_VARIANT[tone]}`;
            drawStages(doc, slots.stages, stage, direction);

            let headline = operation ? operation.headline : "";
            if (pending) {
                headline = pending.reconciling ? "Checking what the server recorded before anything else is allowed…"
                    : pending.kind === "apply" ? "Applying change…" : pending.kind === "reset" ? "Resetting…" : "Retrying the failure-impact analysis…";
            }
            if (slots.headline.textContent !== headline) slots.headline.textContent = headline;     // a live region: only announce a change
            slots.progress.hidden = !(busy || (operation && operation.inProgress));
            const statusMessage = message;
            slots.message.hidden = !statusMessage;
            slots.message.textContent = statusMessage ? statusMessage.text : "";
            if (statusMessage && statusMessage.alert) slots.message.setAttribute("role", "alert");
            else slots.message.removeAttribute("role");

            // Steps are the server's, from what it has persisted. Until it has recorded something there are none to show.
            if (operation && operation.steps.length) {
                drawSteps(doc, slots.steps, operation.steps);
                slots.steps.hidden = false;
            } else {
                slots.steps.replaceChildren();
                slots.steps.hidden = true;
            }

            const analysis = current ? currentAnalysis(current) : null;
            const reset = direction === "reset";
            if (application && operation) {
                drawCards(doc, slots.cards, current, analysis);
                drawBars(doc, slots.bars, analysis, reset);
                drawReduction(doc, slots.reduction, analysis, reset);
                drawPlacement(doc, slots.placement, analysis, reset);
                slots.label.textContent = analysis ? analysis.label : "";
                slots.experiment.textContent = (current.states && current.states.experimentNote) || "";
                drawTechnical(doc, slots, current, analysis, slots.details.open);
                slots.details.hidden = false;
            } else {
                [slots.cards, slots.bars, slots.reduction, slots.placement].forEach((slot) => slot.replaceChildren());
                slots.label.textContent = "";
                slots.experiment.textContent = "";
                slots.details.hidden = true;
            }

            // Controls. While any request is in flight or being reconciled nothing that mutates can be pressed.
            const canReset = !!(current && current.canReset);
            const canRetry = !!(current && current.canRetryAnalysis);
            slots.reset.hidden = !canReset;
            slots.reset.disabled = busy || confirming === "reset";
            slots.retry.hidden = !canRetry;
            slots.retry.disabled = busy;
            const wantsRecheck = !!(operation && (operation.nextAction === "recheck" || (operation.pollMs === null && operation.inProgress)));
            const stoppedWatching = !!(operation && operation.pollMs && !timer && !busy && !inFlight && watchingSince !== null && now() - watchingSince > MAX_AUTO_POLL_MS);
            slots.recheck.hidden = !(wantsRecheck || stoppedWatching);
            slots.recheck.disabled = busy || inFlight;
            const askName = canReset && (confirming === "reset");
            slots["name-wrap"].hidden = !askName;
            if (slots.name.value !== name) slots.name.value = name;       // never rewrite what the person is typing
            slots.name.disabled = busy;
            slots.confirm.hidden = confirming !== "reset";
            slots["confirm-text"].textContent = REVIEW_NOTE.reset;
            slots["confirm-yes"].disabled = busy;
            slots["confirm-no"].disabled = busy;
            root.setAttribute("aria-busy", busy ? "true" : "false");
        }

        // ---- requests ----

        async function request(method, url, body) {
            const response = await fetchImpl(url, {
                method,
                headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
                body: body ? JSON.stringify(body) : undefined,
            });
            let payload = null;
            try {
                payload = await response.json();
            } catch (_error) {
                payload = null;
            }
            return { ok: response.ok, status: response.status, payload };
        }

        /** Take a status from the server unless it is older than what is already shown or from before the person last acted. */
        function adopt(next, order, forEpoch) {
            if (destroyed || forEpoch !== epoch || order < shown) return false;
            shown = order;
            // "Nothing is applied" is the truth only when no request of ours is in flight; while one is, the server may simply not
            // have recorded it yet, and the shell that stands for it must not be wiped.
            if (next && next.application) status = next;
            else if (!pending) status = null;
            backoff = 1;
            if (!watchingSince) watchingSince = now();
            onStatus(next);
            return true;
        }

        function unwatch() {
            if (timer !== null) {
                cancel(timer);
                timer = null;
            }
        }

        function arm() {
            unwatch();
            if (destroyed) return;
            let wait = null;
            if (pending && !pending.reconciling) wait = FAST_POLL_MS;
            else if (!pending && status && status.operation && status.operation.pollMs) {
                if (watchingSince !== null && now() - watchingSince > MAX_AUTO_POLL_MS) return draw();
                wait = status.operation.pollMs * backoff;
            }
            if (wait !== null) timer = schedule(() => { timer = null; poll(); }, wait);
        }

        async function poll() {
            if (destroyed || inFlight || !status) return;
            inFlight = true;
            const order = ++issued;
            const forEpoch = epoch;
            try {
                const result = await request("GET", applicationUrl(status, "/application"));
                if (result.ok) {
                    if (adopt(result.payload, order, forEpoch) && !pending) message = null;
                } else if (forEpoch === epoch) {
                    backoff = Math.min(MAX_BACKOFF, backoff * 2);
                    message = { text: errorMessage(result, "The backend could not report the status. Showing the last recorded state."), alert: true };
                }
            } catch (_error) {
                if (forEpoch === epoch) {
                    backoff = Math.min(MAX_BACKOFF, backoff * 2);
                    message = { text: "The backend cannot be reached. Showing the last recorded state; it will be asked again.", alert: true };
                }
            } finally {
                inFlight = false;
            }
            if (destroyed) return;
            draw();
            arm();
        }

        async function recheck() {
            watchingSince = now();
            backoff = 1;
            unwatch();
            await poll();
        }

        /** Read the server's current application (a page load, or after a cluster change): the way back to an operation after a reload. */
        async function start() {
            const order = ++issued;
            const forEpoch = epoch;
            try {
                const result = await request("GET", `${BASE}/applications/current`);
                if (result.ok && adopt(result.payload, order, forEpoch)) {
                    watchingSince = now();
                    draw();
                    arm();
                }
            } catch (_error) {
                // No operation can be shown without the backend; the page reports that itself.
            }
        }

        /** After a failed or lost request: read the status again, and only then let the person act. */
        async function reconcile(previous) {
            if (!status) return;
            pending.reconciling = true;
            draw();
            const order = ++issued;
            const forEpoch = epoch;
            let outcome = null;
            try {
                const result = await request("GET", applicationUrl(status, "/application"));
                if (result.ok) outcome = result.payload;
            } catch (_error) {
                outcome = null;
            }
            if (forEpoch !== epoch || destroyed) return;
            if (outcome) {
                adopt(outcome, order, forEpoch);
                const recorded = outcome.application;
                const changed = recorded && (!previous || recorded.applicationId !== previous.applicationId || recorded.status !== previous.status);
                if (changed) message = { text: `${message ? message.text + " " : ""}The server's record shows: ${outcome.operation.headline}`, alert: true };
            } else {
                message = { text: `${message ? message.text + " " : ""}The status could not be read just now, so the change may or may not have gone through. Check again before doing anything else.`, alert: true };
                watchingSince = now();
            }
        }

        async function mutate(kind, target, url, body, previous) {
            if (pending || destroyed) return false;          // a second click while one request is in flight does nothing
            epoch += 1;                                       // anything asked before this click is older than it
            const mine = epoch;
            pending = { kind, reconciling: false, previousId: previous ? previous.applicationId : null };
            message = null;
            confirming = null;
            status = target;
            unwatch();
            draw();                                           // "Applying change…" is on screen before the request leaves
            arm();
            let result = null;
            try {
                result = await request("POST", url, body);
            } catch (_error) {
                result = { ok: false, status: 0, payload: null };
            }
            if (mine !== epoch || destroyed) return false;
            if (result.ok) {
                pending = null;
                adopt(result.payload, ++issued, mine);
                watchingSince = now();
            } else {
                message = { text: errorMessage(result, result.status === 0 ? "The request did not return, so the outcome is not known yet." : `The ${kind} did not complete.`), alert: true };
                await reconcile(previous);
                if (mine !== epoch || destroyed) return false;
                pending = null;
                if (status && !status.application) status = null;       // nothing was recorded: there is no operation to show
            }
            draw();
            arm();
            return result.ok;
        }

        /** The person has confirmed Apply for an approved recommendation: send it and show the operation at once. */
        function startApply(recommendationId, tier, appliedBy) {
            const who = (appliedBy || "").trim();
            if (pending || !who) return Promise.resolve(false);
            name = who;
            const previous = status && status.recommendationId === recommendationId ? status.application : null;
            const shell = { recommendationId, tier, application: previous, operation: null, analysis: {}, states: null };
            return mutate("apply", shell, applicationUrl(shell, "/apply"), { appliedBy: who }, previous);
        }

        function doReset() {
            const who = (slots.name.value || "").trim();
            if (pending || !status || !who) {
                if (!who && status) {
                    message = { text: "Enter your name: resetting a change is done by a named person.", alert: true };
                    draw();
                }
                return Promise.resolve(false);
            }
            name = who;
            return mutate("reset", status, applicationUrl(status, "/reset"), { resetBy: who }, status.application);
        }

        function doRetry() {
            if (pending || !status) return Promise.resolve(false);
            return mutate("retry", status, applicationUrl(status, "/analysis/retry"), undefined, status.application);
        }

        function clusterChanged() {
            epoch += 1;
            unwatch();
            status = null;
            pending = null;
            message = null;
            confirming = null;
            watchingSince = null;
            draw();
            return start();
        }

        function destroy() {
            destroyed = true;
            unwatch();
        }

        // `pending` with no status yet (a first Apply from the approval gate) draws from the shell, which has no steps or cards.
        return {
            start, startApply, clusterChanged, destroy, recheck,
            state: () => ({ status, pending, message, confirming, epoch, polling: timer !== null, inFlight }),
        };
    }

    window.ResilienceRecommendationOperation = {
        createOperationPanel,
        APPLY_NOTE: REVIEW_NOTE.apply,
        // exposed for tests
        currentAnalysis,
        percent,
    };
})();
