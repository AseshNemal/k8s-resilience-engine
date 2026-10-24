/*
 * Overview summaries: what the Overview page says about the research result, not only the cluster.
 * Shared page (no single member owns Overview); these helpers were added by Member 4's work and only describe.
 *
 * Every function turns a body the backend already returned into something to show. Nothing here is estimated or defaulted: a piece
 * of evidence that is not there is reported as missing or pending, with the backend's own reason when it gave one. The functions
 * are pure (no DOM, no requests) so they can be tested alone; dashboard.js draws what they return and makes the requests.
 */
(function (global) {
    "use strict";

    const AVAILABLE = "available";
    const MISSING = "missing";
    const INCOMPLETE = "incomplete";
    const PENDING = "pending";

    const text = (value, limit) => String(value === null || value === undefined ? "" : value).slice(0, limit || 300);
    const finite = (value) => typeof value === "number" && Number.isFinite(value);
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    const percent = (value) => (finite(value) ? `${Number(value.toFixed(1))}%` : "unavailable");

    /** The label of a saved run, from the Recommendations page's own function when it is loaded: one definition of what a result is called. */
    function resultOf(row) {
        const label = global.ResilienceApplyTest && global.ResilienceApplyTest.resultLabel;
        if (label) return label(row);
        return [text(row.status, 40) || "Unknown", "unknown"];
    }

    function strategyName(code) {
        const value = text(code, 60).replace(/_/g, " ").toLowerCase();
        return value ? value.charAt(0).toUpperCase() + value.slice(1) : "Candidate";
    }

    /**
     * The current risk: what is lost when the assessed node fails, from the all-workflows recommendation detail. ``detail`` is that
     * body, or null when it could not be read, in which case ``reason`` is the backend's explanation.
     */
    function describeRisk(detail, reason) {
        if (!detail) return { state: reason && reason.pending ? PENDING : MISSING, headline: reason && reason.pending ? "Computing the failure impact…" : "No failure impact yet",
            sub: reason && reason.message ? text(reason.message) : "It needs a connected cluster and a workflow that applies to the application.", lostPercent: null };
        const impact = detail.baselineImpact;
        const node = detail.evidence && detail.evidence.failedNode;
        if (!impact || !finite(impact.blastRadius) || !node) {
            return { state: MISSING, headline: "The failure impact was not reported", sub: "The predictor returned no baseline for this application.", lostPercent: null };
        }
        const total = detail.portfolio && Array.isArray(detail.portfolio.workflows) ? detail.portfolio.workflows.length : null;
        const lost = Array.isArray(impact.failedWorkflows) ? impact.failedWorkflows.length : null;
        const scope = total !== null && lost !== null ? `${lost} of ${plural(total, "workflow")}` : lost !== null ? plural(lost, "workflow") : "the workflow";
        const survives = impact.blastRadius === 0;
        return {
            state: AVAILABLE, survives, lostPercent: impact.blastRadius, node,
            headline: survives ? `If ${node} fails, every workflow survives` : `If ${node} fails, ${scope} ${lost === 1 ? "is" : "are"} lost`,
            sub: `Blast radius ${percent(impact.blastRadius)}${Array.isArray(impact.failedServices) && impact.failedServices.length ? `; services lost: ${impact.failedServices.slice(0, 8).map((s) => text(s, 60)).join(", ")}` : ""}. Predicted from the live topology, not measured.`,
        };
    }

    function evidenceById(context, id) {
        return context && Array.isArray(context.evidence) ? context.evidence.find((item) => item.id === id) : null;
    }

    /**
     * The "where you are" strip: four chips, each linking to the page that moves it on. ``input`` holds what has been learned so far
     * (``discovery``: the cluster answered; ``context``; ``risk``; ``history``), so the strip can be drawn while the rest is loading.
     */
    function describeChips(input) {
        const { discovery, context, risk, history } = input || {};
        const chips = [];
        chips.push({ id: "cluster", label: "Cluster", href: "/components/deployments",
            state: discovery === undefined ? PENDING : discovery ? AVAILABLE : MISSING,
            detail: discovery === undefined ? "Checking…" : discovery ? "Connected and read" : "Not connected, or it cannot be read" });

        let workflow;
        if (!context) workflow = { state: PENDING, detail: "Checking…" };
        else {
            const frozen = (context.workflows || []).filter((w) => w.applicable).length;
            const declared = (context.declaredWorkflows || []).filter((w) => w.applicable).length;
            if (frozen) workflow = { state: AVAILABLE, detail: `${plural(frozen, "frozen workflow")} apply` };
            else if (declared) workflow = { state: AVAILABLE, detail: `${plural(declared, "declared workflow")} apply (declared, not observed)` };
            else {
                const item = evidenceById(context, "workflow") || evidenceById(context, "declaredWorkflow");
                workflow = { state: MISSING, detail: item ? text(item.detail, 160) : "No workflow applies to the application" };
            }
        }
        chips.push({ id: "workflow", label: "Workflow", href: "/components/workflows", ...workflow });

        chips.push({ id: "prediction", label: "Prediction", href: "/components/failure-impacts", state: risk ? risk.state : PENDING,
            detail: !risk ? "Checking…" : risk.state === AVAILABLE ? risk.headline : risk.state === PENDING ? "Computing…" : text(risk.sub, 160) });

        const runs = history && Array.isArray(history.runs) ? history.runs : null;
        chips.push({ id: "test", label: "Fix tested", href: "/components/recommendations",
            state: runs === null ? (history === null ? MISSING : PENDING) : runs.length ? AVAILABLE : MISSING,
            detail: runs === null ? (history === null ? "Saved tests could not be read" : "Checking…")
                : runs.length ? `${plural(runs.length, "saved test")}; latest: ${resultOf(runs[0])[0].toLowerCase()}` : "No fix has been applied and tested yet" });
        return chips;
    }

    /** Applied-change banner: shown only while a change is on the cluster (applying, applied, or a reset that failed). */
    function describeApplied(body) {
        const application = body && body.application;
        const active = ["APPLYING", "APPLIED", "RESET_FAILED"];
        if (!application || !active.includes(application.status)) return { visible: false };
        const names = Array.isArray(application.deployments) ? application.deployments.map((d) => text(d, 80)) : [];
        const status = text(application.status, 30).replace(/_/g, " ").toLowerCase();
        return { visible: true, namespace: text(application.namespace, 80), deployments: names,
            text: `A change is on the cluster: ${text(application.namespace, 80)}${names.length ? ` · ${names.join(", ")}` : ""} (${status}${application.appliedAt ? `, applied ${text(application.appliedAt, 40)}` : ""}). Its results are on the Recommendations page, where it can also be reset.` };
    }

    /** The latest saved test per candidate (newest first), for the results table. Predicted and measured stay apart. */
    function describeResults(history, limit) {
        const runs = history && Array.isArray(history.runs) ? history.runs : [];
        const seen = new Set();
        const rows = [];
        for (const run of runs) {
            const key = `${run.namespace}/${run.candidateId}`;
            if (seen.has(key)) continue;
            seen.add(key);
            const [outcome, outcomeState] = resultOf(run);
            const candidate = run.candidate || {};
            rows.push({
                name: candidate.type ? `${strategyName(candidate.type)}${candidate.service ? ` on ${text(candidate.service, 60)}` : ""}` : text(run.candidateId, 80),
                namespace: text(run.namespace, 60), baseline: percent(run.baselineRisk), predicted: percent(run.predictedRisk), after: percent(run.afterRisk),
                outcome, outcomeState, when: text(run.createdAt, 40), runId: run.runId,
            });
            if (rows.length >= (limit || 6)) break;
        }
        return rows;
    }

    /** The evidence panel: the backend's own checklist, plus what the latest saved test says about workflow checks and resource metrics. */
    function describeEvidence(context, history) {
        const items = (context && Array.isArray(context.evidence) ? context.evidence : [])
            .map((item) => ({ id: text(item.id, 40), label: text(item.label, 80), state: text(item.state, 20), detail: text(item.detail, 300) }));
        const runs = history && Array.isArray(history.runs) ? history.runs : [];
        const latest = runs[0];
        if (!latest) {
            items.push({ id: "workflowChecks", label: "Workflow checks", state: PENDING, detail: "Unknown until a fix is applied and tested." });
            items.push({ id: "metrics", label: "Resource metrics", state: PENDING, detail: "Unknown until a fix is applied and tested." });
            return items;
        }
        const rows = (latest.afterWorkflows && latest.afterWorkflows.rows) || (latest.beforeWorkflows && latest.beforeWorkflows.rows) || [];
        const configured = rows.filter((row) => row.httpStatus && row.httpStatus !== "NOT_CONFIGURED").length;
        items.push({ id: "workflowChecks", label: "Workflow checks", detail: rows.length ? `${configured} of ${plural(rows.length, "workflow")} have an HTTP check configured (latest test).` : "The latest test collected no workflow rows.",
            state: !rows.length ? MISSING : configured === rows.length ? AVAILABLE : configured ? INCOMPLETE : MISSING });
        const measured = latest.afterResources;
        items.push({ id: "metrics", label: "Resource metrics", state: measured === true ? AVAILABLE : measured === false ? MISSING : PENDING,
            detail: measured === true ? "CPU and memory were measured after the change (latest test)." : measured === false ? "CPU and memory usage were unavailable in the latest test." : "The latest test did not reach the measurement step." });
        return items;
    }

    global.ResilienceOverview = { describeRisk, describeChips, describeApplied, describeResults, describeEvidence, AVAILABLE, MISSING, INCOMPLETE, PENDING };
})(typeof window !== "undefined" ? window : globalThis);
