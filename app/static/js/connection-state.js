/*
 * Connection and evidence state helpers shared by every page.
 *
 * Maps the API's stable error codes and the discovery envelope's status to what the page shows. It
 * only describes states the backend actually reported; it never supplies, estimates or substitutes
 * data. A cluster failure is shown as a failure, a connected-but-empty namespace as a valid empty
 * state, and partial discovery as partial.
 */
(function () {
    const MESSAGES = {
        CLUSTER_NOT_CONNECTED: {
            state: "not-connected",
            title: "No cluster connected",
            message: "Connect a Kubernetes cluster from the connection page. Nothing is shown until a cluster is connected.",
        },
        CLUSTER_CONNECTION_FAILED: {
            state: "connection-failed",
            title: "Cannot reach the cluster",
            message: "The selected Kubernetes cluster did not answer. Check that it is running, then retry.",
        },
        CLUSTER_AUTH_FAILED: {
            state: "access-denied",
            title: "The cluster rejected the credential",
            message: "The stored credential may have expired or been revoked. Reconnect the cluster.",
        },
        CLUSTER_FORBIDDEN: {
            state: "access-denied",
            title: "The credential cannot read what is needed",
            message: "The connected credential lacks a required read permission.",
        },
        DISCOVERY_FAILED: {
            state: "discovery-failed",
            title: "Discovery failed",
            message: "The cluster returned an unexpected error. Details are in the server log.",
        },
        INVALID_INPUT: {
            state: "invalid-input",
            title: "Missing or invalid input",
            message: "Check the values entered above.",
        },
        INVALID_NAMESPACE: {
            state: "invalid-input",
            title: "Invalid namespace",
            message: "Enter a valid Kubernetes namespace name.",
        },
        TOPOLOGY_INCOMPLETE: {
            state: "insufficient-evidence",
            title: "Not enough topology evidence",
            message: "Placement of a service this analysis depends on is unresolved, so no result was produced.",
        },
    };
    for (const code of [
        "WORKFLOW_EVIDENCE_UNAVAILABLE",
        "RECOMMENDATION_EVIDENCE_UNAVAILABLE",
        "DECLARED_STATE_UNAVAILABLE",
        "SNAPSHOT_PROVENANCE_UNVERIFIED",
        "APPROVAL_PROVENANCE_UNVERIFIED",
    ]) {
        MESSAGES[code] = {
            state: "evidence-unavailable",
            title: "Required evidence is not available",
            message: "The live evidence this view needs is not available yet.",
        };
    }

    /** Describe a failed request. `error` is what ResilienceApi.fetchJson throws. */
    function describeError(error) {
        const code = (error && error.code) || null;
        const known = code ? MESSAGES[code] : null;
        const backendMessage = error && error.code && error.message ? error.message : null;
        const lines = [];
        const details = (error && error.details) || null;
        if (details && Array.isArray(details.services)) {
            for (const entry of details.services) {
                lines.push(`${entry.service}: ${(entry.reasons || []).join(", ")}`);
            }
        }
        if (known) {
            return {
                state: known.state,
                title: known.title,
                message: backendMessage || known.message,
                code,
                lines,
                retryable: known.state !== "invalid-input",
            };
        }
        if (code) {
            return { state: "error", title: "The request failed", message: backendMessage || "Unexpected error.", code, lines, retryable: true };
        }
        return {
            state: "backend-unreachable",
            title: "The application did not answer",
            message: (error && error.message) || "The application backend did not answer.",
            code: null,
            lines,
            retryable: true,
        };
    }

    /** Describe a successful discovery envelope (status ok / empty / partial). */
    function describeDiscovery(result) {
        const graph = result.graph || { nodes: [], services: [] };
        const notes = [];
        for (const warning of result.warnings || []) notes.push(warning.message);
        for (const item of result.unresolved || []) {
            notes.push(`Unresolved ${item.kind} ${item.name}: ${item.reason}`);
        }
        if (result.declaredState !== "available") {
            notes.push("No declared deployment intent was selected, so manifest-versus-runtime drift is not evaluated.");
        }

        let state = result.status;
        let badge;
        let headline;
        if (result.status === "ok") {
            badge = { variant: "success", text: "Live cluster · evidence complete" };
            headline = `${graph.services.length} services on ${graph.nodes.length} worker nodes in namespace ${result.namespace}.`;
        } else if (result.status === "empty") {
            badge = { variant: "info", text: "Connected · no workloads found" };
            headline = `The cluster is connected, but no matching workloads were found in namespace ${result.namespace}.`;
        } else {
            state = "partial";
            badge = { variant: "warning", text: "Live cluster · partial evidence" };
            headline = `Some of namespace ${result.namespace} could not be resolved. Treat results as incomplete.`;
        }

        const cluster = result.cluster || {};
        const fingerprint = cluster.connectionFingerprint ? cluster.connectionFingerprint.slice(0, 8) : "unknown";
        return {
            state,
            badge,
            headline,
            notes,
            capturedAt: result.capturedAt,
            provenance: `Captured ${result.capturedAt}. Connection fingerprint ${fingerprint}… detects a changed connection; it is not proof of which cluster this is.`,
        };
    }

    function plural(count, word) {
        return `${count} ${word}${count === 1 ? "" : "s"}`;
    }

    const NAMESPACE_BADGES = {
        ok: { variant: "success", text: "Complete" },
        empty: { variant: "info", text: "No workloads" },
        partial: { variant: "warning", text: "Partial" },
    };

    /**
     * Describe the all-namespaces overview envelope (status ok / empty / partial). Every figure is copied
     * from the response; a namespace or service that was not returned is never invented here.
     */
    function describeOverview(result) {
        const namespaces = result.namespaces || [];
        const totals = result.totals || {};
        const excluded = result.excludedNamespaces || [];

        const rows = namespaces.map((entry) => ({
            namespace: entry.namespace,
            status: entry.status,
            badge: NAMESPACE_BADGES[entry.status] || NAMESPACE_BADGES.partial,
            unresolved: (entry.unresolved || []).length,
            unresolvedNotes: (entry.unresolved || []).map((item) => `${item.kind} ${item.name}: ${item.reason}`),
            services: ((entry.graph || {}).services || []).map((service) => ({
                name: service.name,
                replicas: `${service.readyReplicas}/${service.declaredReplicas} ready`,
                nodes: [...new Set((service.pods || []).map((pod) => pod.node))].sort(),
            })),
        }));

        const notes = [];
        if (excluded.length) notes.push(`System namespaces not shown: ${excluded.join(", ")}.`);

        let state = result.status;
        let badge;
        let headline;
        if (result.status === "ok") {
            badge = { variant: "success", text: "Live cluster · evidence complete" };
            headline = `${plural(totals.services, "service")} in ${plural(rows.length, "namespace")} on ${plural(totals.workerNodes, "worker node")}.`;
        } else if (result.status === "empty") {
            badge = { variant: "info", text: "Connected · no workloads found" };
            headline = `The cluster is connected (${plural(totals.workerNodes, "worker node")}), but no workloads were found in any application namespace.`;
        } else {
            state = "partial";
            badge = { variant: "warning", text: "Live cluster · partial evidence" };
            headline = "Some namespaces could not be fully resolved. Treat results as incomplete.";
        }

        const cluster = result.cluster || {};
        const fingerprint = cluster.connectionFingerprint ? cluster.connectionFingerprint.slice(0, 8) : "unknown";
        return {
            state,
            badge,
            headline,
            notes,
            totals: { workerNodes: totals.workerNodes, services: totals.services, unresolved: totals.unresolved },
            rows,
            excluded,
            capturedAt: result.capturedAt,
            provenance: `Captured ${result.capturedAt}. Connection fingerprint ${fingerprint}… detects a changed connection; it is not proof of which cluster this is.`,
        };
    }

    function element(doc, tag, text, attributes = {}) {
        const node = doc.createElement(tag);
        if (text !== undefined) node.textContent = text; // never parsed as HTML
        for (const [name, value] of Object.entries(attributes)) node.setAttribute(name, value);
        return node;
    }

    /**
     * Render a state descriptor into a container using DOM methods and textContent, so no value from
     * the API is ever interpreted as markup.
     */
    function renderStateCard(container, descriptor, options = {}) {
        if (!container) return;
        const doc = options.document || window.document;
        container.removeAttribute("aria-busy");
        const card = element(doc, "div", undefined, {
            class: "state error-state", role: "alert", "aria-live": "assertive", "data-state": descriptor.state,
        });
        card.appendChild(element(doc, "p", descriptor.title, { class: "state__title" }));
        card.appendChild(element(doc, "p", descriptor.message));
        if ((descriptor.lines || []).length) {
            const list = element(doc, "ul");
            for (const line of descriptor.lines) list.appendChild(element(doc, "li", line));
            card.appendChild(list);
        }
        if (descriptor.code) {
            const paragraph = element(doc, "p");
            paragraph.appendChild(element(doc, "code", descriptor.code));
            card.appendChild(paragraph);
        }
        if (descriptor.retryable && options.onRetry) {
            const retry = element(doc, "button", "Retry", {
                type: "button", class: "secondary-button", "data-role": "retry-button",
            });
            retry.addEventListener("click", options.onRetry);
            card.appendChild(retry);
        }
        container.replaceChildren(card);
    }

    /** Apply a badge descriptor ({variant, text}) to a status-badge element. */
    function applyBadge(element, badge) {
        if (!element) return;
        element.className = `status-badge status-badge--${badge.variant} status-badge--no-dot`;
        element.textContent = badge.text;
    }

    window.ResilienceConnection = { describeError, describeDiscovery, describeOverview, renderStateCard, applyBadge };
})();
