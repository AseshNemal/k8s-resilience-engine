/*
 * Failure impact page: the small pure rules the page draws from (Member 3's page).
 *
 * The server (GET /api/failure-impacts/overview) already computed every number: which services lose their last ready
 * replica, which workflows stop, the blast radius. This file only turns that answer into labels, replica dots and links.
 * It computes no research value, estimates nothing, and treats an absent value as unknown (never as zero or 100%).
 * No DOM here, so it is tested on its own.
 */
(function (global) {
    "use strict";

    const MAX_DOTS = 8;

    const VERDICTS = {
        "workflow-lost": { variant: "danger", text: "Workflows stop" },
        "service-down": { variant: "warning", text: "Service down" },
        degraded: { variant: "info", text: "Fewer replicas" },
        "no-impact": { variant: "success", text: "No impact" },
        "no-pods": { variant: "muted", text: "Hosts nothing here" },
    };

    const SERVICE_OUTCOMES = {
        failed: { variant: "danger", text: "Down" },
        degraded: { variant: "warning", text: "Fewer replicas" },
        survives: { variant: "success", text: "Unaffected" },
        already_unavailable: { variant: "muted", text: "Already unavailable" },
    };

    function verdict(name) {
        return VERDICTS[name] || { variant: "muted", text: "Unknown" };
    }

    function serviceOutcome(name) {
        return SERVICE_OUTCOMES[name] || { variant: "muted", text: "Unknown" };
    }

    /** One dot per ready replica before the failure; the ones on the failed node are marked lost. Capped for width. */
    function replicaDots(service) {
        const before = Math.max(0, Number(service.readyBefore) || 0);
        const lost = Math.min(before, Math.max(0, Number(service.lostReady) || 0));
        const shown = Math.min(before, MAX_DOTS);
        const lostShown = Math.min(lost, shown);
        const dots = [];
        for (let index = 0; index < shown; index += 1) dots.push(index < lostShown ? "lost" : "kept");
        return { dots, hidden: before - shown };
    }

    function plural(count, one, many) {
        return `${count} ${count === 1 ? one : (many || one + "s")}`;
    }

    /** The three headline figures for a node. A figure the server could not know is "unknown", never a number. */
    function nodeStats(node) {
        const stats = [
            { label: "Services down", value: String(node.servicesDown.length), tone: node.servicesDown.length ? "danger" : "ok" },
        ];
        if (node.workflowsEvaluated > 0) {
            stats.push({ label: "Workflows stopped", value: `${node.workflowsLost} of ${node.workflowsEvaluated}`,
                tone: node.workflowsLost ? "danger" : "ok" });
            // Weighted by each workflow's criticality: the same figure Recommendations shows for this failure. Falls back to the plain share.
            const loss = node.weightedBlastRadius === null || node.weightedBlastRadius === undefined ? node.blastRadius : node.weightedBlastRadius;
            stats.push({ label: "Importance lost", value: `${Number(loss).toFixed(0)}%`, tone: loss ? "danger" : "ok",
                hint: "Share of workflow importance (criticality) that stops. Recommendations shows the same figure." });
        } else {
            stats.push({ label: "Workflows stopped", value: "unknown", tone: "unknown" });
            stats.push({ label: "Importance lost", value: "unknown", tone: "unknown" });
        }
        return stats;
    }

    /** One short line for the node list. */
    function cardLine(node) {
        const parts = [];
        if (node.servicesDown.length) parts.push(`${node.servicesDown.join(", ")} down`);
        if (node.workflowsEvaluated > 0 && node.workflowsLost) parts.push(`${plural(node.workflowsLost, "workflow")} stop`);
        if (!parts.length && node.servicesDegraded.length) parts.push(`${plural(node.servicesDegraded.length, "service")} with fewer replicas`);
        if (!parts.length) parts.push(node.verdict === "no-pods" ? "Hosts no ready pods here" : "Nothing goes down");
        return parts.join(" · ");
    }

    const HANDOFF_KEY = "resilience.failure-impact.handoff.v1";
    const HANDOFF_MAX_AGE_MS = 10 * 60 * 1000;

    /**
     * What the Recommendations page picks up when "See how to reduce this risk" is followed: the application and node
     * this page showed, bound to the cluster fingerprint it was read from, so another cluster never receives it.
     * Passed through storage, not the address: the extension's pages share Docker Desktop's own query string.
     */
    function handoffRecord(namespace, node, fingerprint, now) {
        return { namespace: namespace || "", failedNode: node || "", fingerprint: fingerprint || "", at: now === undefined ? Date.now() : now };
    }

    function handoffIsFresh(record, now) {
        return !!record && typeof record.at === "number" && (now === undefined ? Date.now() : now) - record.at <= HANDOFF_MAX_AGE_MS
            && typeof record.fingerprint === "string" && record.fingerprint !== "" && typeof record.failedNode === "string" && record.failedNode !== "";
    }

    function basisBadge(basis) {
        const kind = basis && basis.kind;
        if (kind === "frozen") return { variant: "success", text: "Approved workflows" };
        if (kind === "declared") return { variant: "warning", text: "Declared workflows · not observed" };
        if (kind === "mixed") return { variant: "warning", text: "Frozen and declared workflows" };
        return { variant: "muted", text: "No workflow · service level only" };
    }

    /** Same node if it is still there after a refresh, else the worst one (the server lists worst first). */
    function chooseNode(nodes, previous) {
        if (!Array.isArray(nodes) || !nodes.length) return null;
        return nodes.some((node) => node.node === previous) ? previous : nodes[0].node;
    }

    /** The person's own name for a workflow when it has one; the identifier is only a fallback. */
    function workflowLabel(workflow) {
        return (workflow && typeof workflow.name === "string" && workflow.name.trim()) || (workflow && workflow.workflowId) || "";
    }

    function workflowLine(workflow) {
        if (workflow.survives) {
            return workflow.unavailableOptionalServices.length
                ? `Keeps working. Optional ${workflow.unavailableOptionalServices.join(", ")} would be unavailable.`
                : "Every service it needs keeps a ready replica.";
        }
        return `Stops because ${workflow.failedMandatoryServices.join(", ")} would have no ready replica.`;
    }

    global.ResilienceFailureView = {
        verdict, serviceOutcome, replicaDots, nodeStats, cardLine, handoffRecord, handoffIsFresh, HANDOFF_KEY, basisBadge, chooseNode, workflowLine, workflowLabel, MAX_DOTS,
    };
})(typeof window !== "undefined" ? window : globalThis);
