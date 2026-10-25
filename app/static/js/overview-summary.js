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

    // How serious a predicted loss is, from the blast radius (percent, criticality-weighted). The lower bound of each level; a deployment
    // may pass its own through describeRisk's third argument, because what counts as "critical" is a judgement, not a measurement.
    const SEVERITY_THRESHOLDS = { critical: 75, high: 50, moderate: 25 };
    const SEVERITY_LABELS = { critical: "Critical", high: "High", moderate: "Moderate", low: "Low", none: "Resilient" };
    const LOST_SHOWN = 8;                  // workflow names drawn before "+N more"

    function severityOf(blastRadius, thresholds) {
        const t = { ...SEVERITY_THRESHOLDS, ...(thresholds || {}) };
        const level = blastRadius <= 0 ? "none" : blastRadius >= t.critical ? "critical" : blastRadius >= t.high ? "high" : blastRadius >= t.moderate ? "moderate" : "low";
        return { level, label: SEVERITY_LABELS[level] };
    }

    /** One plain sentence for the card: what stopping the assessed node is predicted to do, worded for the case. */
    function riskSentence(node, lost, total, survives) {
        if (survives) return `Losing ${node} is not predicted to stop any workflow`;
        if (lost === null) return `Losing ${node} would stop the workflow`;
        if (total === null) return `Losing ${node} would stop ${plural(lost, "workflow")}`;
        if (total > 0 && lost >= total) return total === 1 ? `Losing ${node} would stop the workflow` : `Losing ${node} would stop all ${total} workflows`;
        return `Losing ${node} would stop ${lost} of ${plural(total, "workflow")}`;
    }

    /**
     * The current risk: what is lost when the assessed node fails, from the all-workflows recommendation detail. ``detail`` is that
     * body, or null when it could not be read, in which case ``reason`` is the backend's explanation.
     */
    function describeRisk(detail, reason, options) {
        if (!detail) return { state: reason && reason.pending ? PENDING : MISSING, headline: reason && reason.pending ? "Computing the failure impact…" : "No failure impact yet",
            sub: reason && reason.message ? text(reason.message) : "It needs a connected cluster and a workflow that applies to the application.", lostPercent: null };
        const impact = detail.baselineImpact;
        const node = detail.evidence && detail.evidence.failedNode;
        if (!impact || !finite(impact.blastRadius) || !node) {
            return { state: MISSING, headline: "The failure impact was not reported", sub: "The predictor returned no baseline for this application.", lostPercent: null };
        }
        const total = detail.portfolio && Array.isArray(detail.portfolio.workflows) ? detail.portfolio.workflows.length : null;
        const lost = Array.isArray(impact.failedWorkflows) ? impact.failedWorkflows.length : null;
        const survives = impact.blastRadius === 0;
        const names = Array.isArray(impact.failedWorkflows) ? impact.failedWorkflows.map((w) => text(w, 80)).filter(Boolean) : [];
        const services = Array.isArray(impact.failedServices) && impact.failedServices.length
            ? ` · services affected: ${impact.failedServices.slice(0, 8).map((s) => text(s, 60)).join(", ")}` : "";
        return {
            state: AVAILABLE, survives, lostPercent: impact.blastRadius, node,
            severity: severityOf(impact.blastRadius, options && options.thresholds),
            lostWorkflows: names.slice(0, LOST_SHOWN), moreLost: Math.max(0, names.length - LOST_SHOWN),
            headline: riskSentence(node, lost, total, survives),
            sub: `Blast radius ${percent(impact.blastRadius)}${services} · predicted from the live topology, not measured.`,
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
        const outcome = runs && runs.length ? resultOf(runs[0]) : null;
        chips.push({ id: "test", label: "Latest test", href: "/components/recommendations", statusLabel: outcome && outcome[0],
            state: runs === null ? (history === null ? MISSING : PENDING) : !runs.length ? MISSING
                : outcome[1] === "passed" ? AVAILABLE : outcome[1] === "failed" ? MISSING : INCOMPLETE,
            detail: runs === null ? (history === null ? "Saved tests could not be read" : "Checking…")
                : runs.length ? `${plural(runs.length, "saved test")}; latest: ${resultOf(runs[0])[0].toLowerCase()}` : "No fix has been applied and tested yet" });
        return chips;
    }

    /** Applied-change banner: shown only while a change is on the cluster (applying, applied, or a reset that failed). */
    function describeApplied(body) {
        const application = body && body.application;
        const active = ["APPLYING", "APPLIED", "RESET_FAILED"];
        if (!application || body.clusterMatch === "other" || !active.includes(application.status)) return { visible: false };
        const names = Array.isArray(application.deployments) ? application.deployments.map((d) => text(d, 80)) : [];
        const status = text(application.status, 30).replace(/_/g, " ").toLowerCase();
        return { visible: true, namespace: text(application.namespace, 80), deployments: names,
            text: `${text(application.namespace, 80)}${names.length ? ` · ${names.join(", ")}` : ""} · ${status}${body.clusterMatch === "unknown" ? " · cluster identity unconfirmed" : ""}` };
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
                outcome, outcomeState, when: text(run.createdAt, 40), runId: run.runId, cluster: text(run.clusterName || run.clusterUid || "Unverified cluster", 100),
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

    /** Display data only. Survival and per-node impact come from the predictor, never from replica counts. */
    function describeImpact(detail) {
        const ranking = detail && Array.isArray(detail.nodeRiskRanking) ? detail.nodeRiskRanking : [];
        const nodes = ranking.filter((r) => r.node && finite(r.blastRadius) && r.blastRadius >= 0 && r.blastRadius <= 100)
            .map((r) => ({ node: text(r.node), value: r.blastRadius })).sort((a, b) => b.value - a.value || a.node.localeCompare(b.node));
        const exposure = detail && detail.workflowExposure && detail.workflowExposure.before;
        const assessed = detail && detail.evidence && detail.evidence.failedNode;
        const definitions = detail && Array.isArray(detail.workflowDefinitions) ? detail.workflowDefinitions : [];
        const lostServices = detail && detail.baselineImpact && detail.baselineImpact.failedServices;
        const workflows = ((detail && detail.portfolio && detail.portfolio.workflows) || []).map((w) => {
            const failures = exposure && exposure[w.workflowId];
            const known = Array.isArray(failures) && !!assessed;
            const failed = known ? failures.includes(assessed) : null;
            const definition = definitions.find((d) => (d.workflowId || d.workflow_id) === w.workflowId);
            const mandatory = definition && (definition.mandatoryServices || definition.mandatory_services);
            const causes = failed && Array.isArray(mandatory) && Array.isArray(lostServices) ? mandatory.filter((s) => lostServices.includes(s)) : [];
            return { name: text(w.workflowId), failed, causes, failures: Array.isArray(failures) ? failures : null };
        });
        return { nodes, workflows, assessed };
    }

    /** Observed pod counts, retaining unknown readiness and unresolved placements explicitly. */
    function describeMatrix(discovery) {
        const namespaces = discovery && Array.isArray(discovery.namespaces) ? discovery.namespaces : [];
        const nodes = [...new Set(namespaces.flatMap((ns) => (ns.graph && ns.graph.nodes || []).map((n) => n.name)))];
        const rows = namespaces.flatMap((ns) => (ns.graph && ns.graph.services || []).map((service) => {
            const pods = Array.isArray(service.pods) ? service.pods : null;
            const cells = nodes.map((node) => {
                if (!pods) return null;
                const placed = pods.filter((p) => p.node === node);
                return placed.some((p) => typeof p.ready !== "boolean") ? null : placed.filter((p) => p.ready === true).length;
            });
            const ready = pods ? pods.filter((p) => p.ready === true) : [];
            const unresolved = pods ? pods.filter((p) => !nodes.includes(p.node)).length : null;
            const concentrated = !!pods && pods.every((p) => typeof p.ready === "boolean") && ready.length > 0
                && ready.every((p) => p.node === ready[0].node) && nodes.includes(ready[0].node);
            return { namespace: text(ns.namespace), service: text(service.name), cells, concentrated, unresolved,
                unready: pods && pods.every((p) => typeof p.ready === "boolean") ? pods.filter((p) => p.ready === false).length : null };
        }));
        return { nodes, rows };
    }

    function describeService(service) {
        const ready = service && service.readyReplicas;
        const declared = service && service.declaredReplicas;
        const count = (n) => Number.isInteger(n) && n >= 0;
        const known = count(ready) && count(declared);
        const share = service && service.replicaConcentration;
        return {
            replicas: known ? `${ready}/${declared} ready` : "Replica counts unavailable",
            readyShare: known && declared > 0 ? Math.min(1, ready / declared) : null,
            readiness: !known || declared === 0 ? "unknown" : ready < declared ? "incomplete" : "available",
            concentrated: service && typeof service.concentrated === "boolean" && count(ready) && ready > 0 ? service.concentrated : null,
            concentration: finite(share) && share >= 0 && share <= 1 && count(ready) && ready > 0 ? share : null,
        };
    }

    const CHANGES = {
        TOPOLOGY_SPREAD: "Add topology spread", POD_ANTI_AFFINITY: "Require pod anti-affinity",
        REPLICA_INCREASE: "Add one replica and spread", REPLICA_INCREASE_PLUS_2: "Add two replicas and spread",
        REPLICA_INCREASE_ANTI_AFFINITY: "Add one replica with anti-affinity", COMBINATION: "Review combined change",
    };
    function describeAction(detail, reason) {
        const candidate = detail && (detail.candidates || []).find((c) => c.selected);
        if (!candidate) return { title: "No fix selected", changes: [], benefit: null, cost: "Not reported", costNote: "",
            explanation: text(reason || detail?.outcome?.message || "Review the available evidence."), reviewable: false };
        const parts = Array.isArray(candidate.parts) && candidate.parts.length ? candidate.parts : [candidate];
        return { title: candidate.type === "COMBINATION" ? "Review the combined fix" : CHANGES[candidate.type] || strategyName(candidate.type),
            changes: parts.map((p) => ({ service: text(p.service, 120), change: CHANGES[p.type] || strategyName(p.type) })),
            benefit: finite(candidate.predicted?.riskReduction) ? candidate.predicted.riskReduction : null,
            cost: text(candidate.cost?.resource || "Not reported", 40), costNote: candidate.type === "COMBINATION"
                ? "The combined cost class is the highest member's class, not total resource usage. Review each listed change for added replicas. Rollout disruption is not modelled."
                : text(candidate.cost?.note, 800),
            explanation: text(candidate.recommendedChange, 800), reviewable: true };
    }

    /** Link an existing prediction to observed placement; never infer service or workflow survival here. */
    function describeFocus(detail, workflowName, node) {
        const graph = detail && detail.currentDeployment;
        if (!graph || !(graph.nodes || []).some((n) => n.name === node)) return null;
        const services = Array.isArray(graph.services) ? graph.services : [];
        const hosted = services.filter((s) => (s.pods || []).some((p) => p.node === node)).map((s) => s.name);
        if (!workflowName) return { node, workflow: null, namespace: detail.evidence?.namespace || null, services: hosted,
            message: `Placement on ${node}: ${hosted.length ? hosted.join(", ") : "no service pods observed"}.` };
        const workflow = describeImpact(detail).workflows.find((w) => w.name === workflowName);
        if (!workflow) return null;
        const definition = (detail.workflowDefinitions || []).find((w) => (w.workflowId || w.workflow_id) === workflowName);
        const references = definition ? [...(definition.mandatoryServices || definition.mandatory_services || []),
            ...(definition.optionalServices || definition.optional_services || []), ...(definition.edges || []).flatMap((e) => [e.source, e.target])] : null;
        const causesKnown = node === detail.evidence?.failedNode && workflow.causes.length > 0;
        const related = causesKnown ? workflow.causes : references ? hosted.filter((s) => references.includes(s)) : [];
        const outcome = workflow.failures === null ? "Outcome unknown" : workflow.failures.includes(node) ? "Predicted to fail" : "Predicted to survive";
        return { node, workflow: workflowName, namespace: detail.evidence?.namespace || null, services: related,
            message: `${workflowName} · ${node}: ${outcome}. ${causesKnown ? `Services lost: ${related.join(", ")}.` : references ? `Dependencies observed here: ${related.length ? related.join(", ") : "none"}.` : "Workflow dependency details unavailable."}` };
    }

    global.ResilienceOverview = { describeRisk, describeChips, describeApplied, describeResults, describeEvidence, describeImpact, describeMatrix,
        describeService, describeAction, describeFocus, severityOf, SEVERITY_THRESHOLDS, AVAILABLE, MISSING, INCOMPLETE, PENDING };
})(typeof window !== "undefined" ? window : globalThis);
