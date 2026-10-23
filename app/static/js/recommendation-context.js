/*
 * Recommendations page: which cluster, application and frozen workflow to recommend for (Member 4's area).
 *
 * The page no longer asks for IDs. It asks the backend what the connected cluster and the approved frozen workflows
 * already say (GET /api/recommendations/context), uses the only application and the only applicable workflow
 * automatically, offers a dropdown when there are several, and asks for the explained recommendation
 * (GET /api/recommendations/live/detail) once every piece of evidence is there. When a piece is missing it says which
 * and what to do next; it never fills a gap, never picks between several candidates for you, and never reads "no
 * recommendation" as "healthy".
 *
 * This file is pure logic over an injected environment: it reads only (GET), computes no research value, holds no state
 * outside memory (nothing is remembered between pages or clusters), and builds no workflow or recommendation.
 *
 * Stale protection. Every choice and every cluster change starts a new generation; an answer that arrives for an older
 * generation, or for a cluster that is no longer the connected one, is dropped. Choices belong to one cluster: when
 * the connected cluster changes (or the same connection now reaches a different cluster) the namespace, workflow and
 * node you had chosen are cleared, so nothing chosen on one cluster is ever sent to another.
 *
 * Declared workflows. When Component 1 has no frozen workflow, a person can declare one (a file on the backend). The context
 * lists those apart, they are offered in the same dropdown clearly labelled, they are NEVER chosen for you, and choosing one asks
 * a different route (/declared/detail) whose answer has approval switched off. A declared workflow is not observed and not approved.
 */
(function () {
    "use strict";

    const CONTEXT_URL = "/api/recommendations/context";
    const DETAIL_URL = "/api/recommendations/live/detail";
    const DECLARED_DETAIL_URL = "/api/recommendations/declared/detail";
    const DECLARED_PREFIX = "declared:";

    // What to do next, by coded failure. Wording is fixed here; the server's own message is shown beside it.
    const NEXT_ACTIONS = {
        WORKFLOW_EVIDENCE_UNAVAILABLE: "Open Workflow dependencies, approve the assessment and freeze this workflow's graph, then return here.",
        TOPOLOGY_INCOMPLETE: "Open Deployment topology to see which pods could not be placed. A recommendation needs every service the workflow uses to be fully placed.",
        RECOMMENDATION_EVIDENCE_UNAVAILABLE: "Choose an approved frozen workflow above.",
        CLUSTER_NOT_CONNECTED: "Connect a cluster on the Connect page.",
        CLUSTER_CONNECTION_FAILED: "Check that the cluster is running; reload when it answers.",
        CLUSTER_AUTH_FAILED: "Reconnect on the Connect page; the extension's credential was rejected.",
        CLUSTER_FORBIDDEN: "The extension's credential cannot read what discovery needs; reconnect on the Connect page.",
        DECLARED_WORKFLOW_UNAVAILABLE: "Declare this workflow in the file the backend names (RESILIENCE_DECLARED_WORKFLOW_PATH), or choose another.",
        DECLARED_WORKFLOW_INVALID: "Fix the declaration file named by RESILIENCE_DECLARED_WORKFLOW_PATH.",
    };

    const text = (value, max) => (typeof value === "string" ? value.slice(0, max) : "");
    const list = (value, max) => (Array.isArray(value) ? value.slice(0, max) : []);

    function contextUrl(namespace, workflowId) {
        const params = new URLSearchParams();
        if (namespace) params.set("namespace", namespace);
        if (workflowId) params.set("workflow_id", workflowId);
        const query = params.toString();
        return query ? `${CONTEXT_URL}?${query}` : CONTEXT_URL;
    }

    function detailUrl(workflowId, namespace, failedNode, declared) {
        const params = new URLSearchParams();
        params.set("workflow_id", workflowId);
        params.set("namespace", namespace);
        if (failedNode) params.set("failed_node", failedNode);
        return `${declared === true ? DECLARED_DETAIL_URL : DETAIL_URL}?${params.toString()}`;
    }

    // A coded evidence error, turned into what the person needs: which evidence is missing, and the next action.
    function explainError(error) {
        const code = (error && error.code) || null;
        const details = (error && error.details) || null;
        const lines = [];
        if (details && Array.isArray(details.services)) {
            for (const entry of details.services) lines.push(`${text(entry.service, 80)}: ${list(entry.reasons, 6).map((r) => text(r, 80)).join(", ")}`);
        }
        return {
            code,
            message: text(error && error.message, 400) || "The request failed.",
            lines,
            nextAction: (code && NEXT_ACTIONS[code]) || null,
            retryable: code !== "INVALID_RECOMMENDATION_REQUEST",
        };
    }

    // The failure-impact line of the checklist, from what the recommendation request actually returned.
    function failureImpactItem(detail, error) {
        if (detail) {
            const e = detail.evidence || {};
            return {
                id: "failureImpact", label: "Failure impact", state: "available",
                detail: `Predicted blast radius ${Number(e.blastRadiusBefore).toFixed(0)}% if ${text(e.failedNode, 80)} fails (${text(e.failedNodeChosen, 60)}).`,
                nextAction: null,
            };
        }
        const info = explainError(error);
        const incomplete = info.code === "TOPOLOGY_INCOMPLETE";
        return {
            id: "failureImpact", label: "Failure impact", state: incomplete ? "incomplete" : "missing",
            detail: info.message + (info.lines.length ? ` (${info.lines.join("; ")})` : ""),
            nextAction: info.nextAction,
        };
    }

    function workflowOption(workflow) {
        const parts = [workflow.workflowId];
        if (workflow.frozenAt) parts.push(`frozen ${workflow.frozenAt.slice(0, 10)}`);
        parts.push(`${workflow.services.length} services`);
        return {
            workflowId: workflow.workflowId,
            value: workflow.workflowId, declared: false,
            label: parts.join(" · ") + (workflow.applicable ? "" : ` — not usable here: ${workflow.reason}`),
            applicable: workflow.applicable,
            reason: workflow.reason || null,
        };
    }

    // A declared workflow in the dropdown: always labelled as declared and not approvable, and its value carries the prefix
    // that tells a choice of it apart from a frozen workflow of the same name.
    function declaredOption(workflow) {
        const by = workflow.declaredBy ? ` by ${workflow.declaredBy}` : "";
        return {
            workflowId: workflow.workflowId,
            value: DECLARED_PREFIX + workflow.workflowId, declared: true,
            label: `${workflow.workflowId} · declared${by} · ${workflow.services.length} services — declared, not approvable`
                + (workflow.applicable ? "" : ` — not usable here: ${workflow.reason}`),
            applicable: workflow.applicable,
            reason: workflow.reason || null,
        };
    }

    function cleanContext(body) {
        if (!body || typeof body !== "object" || !Array.isArray(body.evidence) || !Array.isArray(body.workflows) || !Array.isArray(body.namespaces)) {
            throw new Error("unexpected context response");
        }
        return {
            cluster: { state: text(body.cluster && body.cluster.state, 20), fingerprint: text(body.cluster && body.cluster.fingerprint, 200) || null },
            namespaces: body.namespaces.map((row) => ({ namespace: text(row.namespace, 253), services: list(row.services, 100).map((s) => text(s, 253)) })),
            namespace: text(body.namespace, 253) || null,
            namespaceChoice: text(body.namespaceChoice, 30),
            workflows: body.workflows.map((w) => ({
                workflowId: text(w.workflowId, 200), freezeId: text(w.freezeId, 200) || null, frozenAt: text(w.frozenAt, 40) || null,
                services: list(w.services, 100).map((s) => text(s, 253)), applicable: w.applicable === true, reason: text(w.reason, 300) || null,
            })),
            declaredWorkflows: (Array.isArray(body.declaredWorkflows) ? body.declaredWorkflows : []).map((w) => ({
                workflowId: text(w.workflowId, 200), declaredBy: text(w.declaredBy, 100) || null,
                services: list(w.services, 100).map((s) => text(s, 253)), applicable: w.applicable === true, reason: text(w.reason, 300) || null,
            })),
            workflowId: text(body.workflowId, 200) || null,
            workflowChoice: text(body.workflowChoice, 30),
            evidence: body.evidence.map((e) => ({ id: text(e.id, 40), label: text(e.label, 80), state: text(e.state, 20), detail: text(e.detail, 600), nextAction: text(e.nextAction, 600) || null })),
            nextActions: list(body.nextActions, 10).map((a) => text(a, 600)),
            ready: body.ready === true,
        };
    }

    // env: { fetchJson(url) -> body (rejects with error.code/.details for coded errors), clusterIdentity() -> string,
    //        onUpdate(view) }
    function createController(env) {
        const s = {
            identity: null, fingerprint: null, generation: 0,
            namespace: "", workflowId: "", workflowKind: "frozen", failedNode: "",
            ctx: null, detail: null, error: null, status: "loading",
        };

        function nodesOf(detail) {
            const nodes = detail && detail.currentDeployment && Array.isArray(detail.currentDeployment.nodes) ? detail.currentDeployment.nodes : [];
            return nodes.map((n) => text(n && n.name, 253)).filter(Boolean);
        }

        function view() {
            const ctx = s.ctx;
            const evidence = ctx ? ctx.evidence.map((e) => (e.id === "failureImpact" && (s.detail || s.error) ? failureImpactItem(s.detail, s.error) : e)) : [];
            return {
                status: s.status,
                cluster: { identity: s.identity || "", fingerprint: s.fingerprint },
                namespaces: ctx ? ctx.namespaces : [],
                namespace: s.namespace, namespaceChoice: ctx ? ctx.namespaceChoice : "",
                workflows: ctx ? [...ctx.workflows.map(workflowOption), ...ctx.declaredWorkflows.map(declaredOption)] : [],
                workflowId: s.workflowId, workflowChoice: ctx ? ctx.workflowChoice : "",
                workflowKind: s.workflowKind,
                workflowValue: s.workflowKind === "declared" ? DECLARED_PREFIX + s.workflowId : s.workflowId,
                failedNodes: nodesOf(s.detail), failedNode: s.failedNode,
                evidence,
                nextActions: ctx ? ctx.nextActions : [],
                detail: s.detail,
                error: s.error ? explainError(s.error) : null,
            };
        }

        function publish() { if (env.onUpdate) env.onUpdate(view()); }

        // A choice or request is current only while nothing newer started and the same cluster is still connected.
        const current = (generation) => generation === s.generation;

        async function loadDetail(generation) {
            if (!s.workflowId || !s.namespace) return;
            s.status = "loading-recommendation";
            s.detail = null; s.error = null;
            publish();
            try {
                const detail = await env.fetchJson(detailUrl(s.workflowId, s.namespace, s.failedNode, s.workflowKind === "declared"));
                if (!current(generation)) return;               // an older request: never shown
                s.detail = detail;
                s.status = "ready";
            } catch (error) {
                if (!current(generation)) return;
                s.error = error;
                s.status = "blocked";
            }
            publish();
        }

        async function start() {
            const generation = ++s.generation;
            s.status = "loading"; s.detail = null; s.error = null;
            publish();

            const identity = text(await env.clusterIdentity(), 253);
            if (!current(generation)) return view();
            if (s.identity !== null && identity !== s.identity) {   // a different cluster than the choices were made on
                s.namespace = ""; s.workflowId = ""; s.workflowKind = "frozen"; s.failedNode = ""; s.ctx = null;
            }
            s.identity = identity;

            let ctx;
            try {
                // A declared choice is not a frozen workflow, so it is never sent to the context as one.
                ctx = cleanContext(await env.fetchJson(contextUrl(s.namespace, s.workflowKind === "declared" ? "" : s.workflowId)));
            } catch (error) {
                if (!current(generation)) return view();
                s.ctx = null; s.error = error; s.status = "blocked";
                publish();
                return view();
            }
            if (!current(generation)) return view();
            if (s.fingerprint !== null && ctx.cluster.fingerprint !== s.fingerprint) {
                // The same connection now reaches another cluster. This answer was asked for with the old cluster's
                // choices, so it is discarded whole: the choices are cleared and the question is asked again with
                // none. No recommendation is ever requested from a context that does not match its cluster.
                s.namespace = ""; s.workflowId = ""; s.workflowKind = "frozen"; s.failedNode = ""; s.ctx = null;
                s.fingerprint = ctx.cluster.fingerprint;
                return start();
            }
            s.fingerprint = ctx.cluster.fingerprint;
            s.ctx = ctx;
            s.namespace = ctx.namespace || "";
            if (s.workflowKind === "declared") {
                // Kept only while that declared workflow still exists and applies here; otherwise the choice is dropped, not guessed.
                const kept = ctx.declaredWorkflows.some((d) => d.workflowId === s.workflowId && d.applicable);
                if (!kept || !s.namespace) { s.workflowKind = "frozen"; s.workflowId = ctx.workflowId || ""; }
            } else {
                s.workflowId = ctx.workflowId || "";
            }
            if (s.workflowKind === "declared") {
                await loadDetail(generation);
                return view();
            }
            if (!ctx.ready) {
                // Several applications or several applicable workflows is a choice for you; anything else is missing evidence.
                s.status = ctx.namespaceChoice === "ambiguous" || ctx.workflowChoice === "choose" ? "needs-input" : "blocked";
                publish();
                return view();
            }
            await loadDetail(generation);
            return view();
        }

        function reset() {
            s.generation += 1;
            s.namespace = ""; s.workflowId = ""; s.workflowKind = "frozen"; s.failedNode = "";
            s.ctx = null; s.detail = null; s.error = null; s.fingerprint = null; s.identity = null;
        }

        return {
            start,
            reload: start,
            selectNamespace(namespace) { s.namespace = text(namespace, 253); s.workflowId = ""; s.workflowKind = "frozen"; s.failedNode = ""; return start(); },
            selectWorkflow(value) {
                const chosen = text(value, 220);
                s.workflowKind = chosen.startsWith(DECLARED_PREFIX) ? "declared" : "frozen";
                s.workflowId = s.workflowKind === "declared" ? chosen.slice(DECLARED_PREFIX.length) : chosen;
                s.failedNode = "";
                return start();
            },
            selectFailedNode(node) { s.failedNode = text(node, 253); const generation = ++s.generation; return loadDetail(generation); },
            // The connected cluster changed: nothing chosen before belongs to the new one.
            clusterChanged() { reset(); return start(); },
            // The inputs an approval request must send, because the server recomputes the recommendation from them.
            selection() { return { workflowId: s.workflowId, namespace: s.namespace, failedNode: s.failedNode, workflowKind: s.workflowKind }; },
            view,
        };
    }

    window.ResilienceRecommendationContext = { createController, explainError, contextUrl, detailUrl, failureImpactItem, cleanContext, NEXT_ACTIONS };
})();
