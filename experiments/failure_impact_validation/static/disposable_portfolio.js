(function (global) {
    "use strict";

    const state = {
        scenarioId: null,
        generation: null,
        results: null,
    };

    function escapeHtml(value) {
        return String(value ?? "")
            .replaceAll("&", "&amp;")
            .replaceAll("<", "&lt;")
            .replaceAll(">", "&gt;")
            .replaceAll('"', "&quot;")
            .replaceAll("'", "&#039;");
    }

    function graphSummaries(crossLayerArtifact) {
        return (crossLayerArtifact?.graphs || []).map((graph) => ({
            workflowId: graph.workflowId,
            nodeCount: graph.nodes.length,
            edgeCount: graph.edges.length,
            mandatoryServices: [...graph.mandatoryServices],
            optionalServices: [...graph.optionalServices],
            unresolvedServices: [...graph.unresolvedServices],
        }));
    }

    function resultRows(resultArtifact) {
        return (resultArtifact?.results || []).map((result) => ({
            failedNode: result.failedNode,
            failedWorkflowCount: result.failedWorkflows.length,
            failedWorkflows: [...result.failedWorkflows],
            failedServices: [...result.failedServices],
            blastRadius: result.blastRadius,
            survivability: result.survivability,
        }));
    }

    async function postJson(endpoint, payload) {
        const response = await fetch(endpoint, {
            method: "POST",
            headers: {
                Accept: "application/json",
                "Content-Type": "application/json",
            },
            body: JSON.stringify(payload),
        });
        let body = null;
        try {
            body = await response.json();
        } catch (_error) {
            body = null;
        }
        if (!response.ok) {
            throw new Error(body?.detail || `Request failed with HTTP ${response.status}`);
        }
        return body;
    }

    function downloadJson(filename, payload) {
        const blob = new Blob([`${JSON.stringify(payload, null, 2)}\n`], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url;
        anchor.download = filename;
        anchor.click();
        URL.revokeObjectURL(url);
    }

    function renderGeneration(payload) {
        const workflows = payload.workflowArtifact.workflows;
        const deployment = payload.deploymentGraph;
        const graphs = payload.crossLayerArtifact;
        const summaries = graphSummaries(graphs);

        document.getElementById("portfolio-summary").innerHTML = [
            ["Workflows", workflows.length],
            ["Services", deployment.services.length],
            ["Worker nodes", deployment.nodes.length],
            ["Cross-layer graphs", graphs.workflowCount],
        ].map(([label, value]) => (
            `<div class="metric"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`
        )).join("");

        document.getElementById("workflow-list").innerHTML = workflows.map((workflow) => `
            <article class="workflow-card">
                <h3>${escapeHtml(workflow.workflowId)}</h3>
                <small>Criticality ${escapeHtml(workflow.criticality)}</small>
                <p><strong>Mandatory:</strong> ${escapeHtml(workflow.mandatoryServices.join(", ") || "none")}</p>
                <p><strong>Optional:</strong> ${escapeHtml(workflow.optionalServices.join(", ") || "none")}</p>
            </article>
        `).join("");

        document.getElementById("deployment-table").innerHTML = `
            <thead><tr><th>Service</th><th>Ready replicas</th><th>Concentration</th><th>Pod placement</th></tr></thead>
            <tbody>${deployment.services.map((service) => `
                <tr>
                    <td>${escapeHtml(service.name)}</td>
                    <td>${escapeHtml(service.readyReplicas)} / ${escapeHtml(service.declaredReplicas)}</td>
                    <td>${escapeHtml((service.replicaConcentration * 100).toFixed(0))}%</td>
                    <td>${service.pods.map((pod) => `${escapeHtml(pod.name)} → ${escapeHtml(pod.node)}`).join("<br>")}</td>
                </tr>
            `).join("")}</tbody>`;

        document.getElementById("cross-layer-graphs").innerHTML = graphs.graphs.map((graph, index) => {
            const summary = summaries[index];
            return `
                <details${index === 0 ? " open" : ""}>
                    <summary>${escapeHtml(graph.workflowId)} · ${summary.nodeCount} nodes · ${summary.edgeCount} edges</summary>
                    <div class="graph-body">
                        <div class="graph-meta">
                            <span class="pill">Mandatory: ${escapeHtml(summary.mandatoryServices.join(", ") || "none")}</span>
                            <span class="pill">Optional: ${escapeHtml(summary.optionalServices.join(", ") || "none")}</span>
                            <span class="pill">Unresolved: ${escapeHtml(summary.unresolvedServices.join(", ") || "none")}</span>
                        </div>
                        <div class="table-wrap"><table>
                            <thead><tr><th>Source</th><th>Relationship</th><th>Target</th></tr></thead>
                            <tbody>${graph.edges.map((edge) => `
                                <tr><td>${escapeHtml(edge.source)}</td><td>${escapeHtml(edge.relationship)}</td><td>${escapeHtml(edge.target)}</td></tr>
                            `).join("")}</tbody>
                        </table></div>
                    </div>
                </details>`;
        }).join("");

        const select = document.getElementById("failed-node");
        select.innerHTML = deployment.nodes.map((node) => (
            `<option value="${escapeHtml(node.name)}">${escapeHtml(node.name)}</option>`
        )).join("");
        document.getElementById("generated-section").classList.remove("hidden");
    }

    function renderResults(resultArtifact) {
        const rows = resultRows(resultArtifact);
        document.getElementById("result-table").innerHTML = `
            <thead><tr><th>Failed node</th><th>Failed workflows</th><th>Failed services</th><th>Blast radius</th><th>Survivability</th></tr></thead>
            <tbody>${rows.map((row) => `
                <tr>
                    <td>${escapeHtml(row.failedNode)}</td>
                    <td>${escapeHtml(row.failedWorkflows.join(", ") || "none")}</td>
                    <td>${escapeHtml(row.failedServices.join(", ") || "none")}</td>
                    <td class="${row.blastRadius ? "bad" : "good"}">${escapeHtml(row.blastRadius.toFixed(1))}%</td>
                    <td class="${row.survivability === 100 ? "good" : "bad"}">${escapeHtml(row.survivability.toFixed(1))}%</td>
                </tr>
            `).join("")}</tbody>`;

        document.getElementById("workflow-outcomes").innerHTML = resultArtifact.results.map((result) => `
            <article class="outcome-card">
                <h3>${escapeHtml(result.failedNode)}</h3>
                ${result.workflowImpacts.map((impact) => `
                    <p>
                        <strong class="${impact.survives ? "good" : "bad"}">${impact.survives ? "SURVIVES" : "FAILS"}</strong>
                        · ${escapeHtml(impact.workflowId)}
                        · unavailable: ${escapeHtml(impact.failedServices.join(", ") || "none")}
                        · mandatory failures: ${escapeHtml(impact.failedMandatoryServices.join(", ") || "none")}
                    </p>
                `).join("")}
            </article>
        `).join("");
        document.getElementById("results-section").classList.remove("hidden");
        document.getElementById("results-section").scrollIntoView({ behavior: "smooth", block: "start" });
    }

    function setBusy(busy, message, error = false) {
        ["generate-button", "fail-one-button", "fail-all-button"].forEach((id) => {
            const element = document.getElementById(id);
            if (element) element.disabled = busy;
        });
        const status = document.getElementById("status");
        status.textContent = message;
        status.classList.toggle("error", error);
    }

    function currentScenarioId() {
        const value = document.getElementById("scenario-id").value.trim();
        if (!value) throw new Error("Enter a scenario ID before continuing.");
        return value;
    }

    async function generate() {
        try {
            setBusy(true, "Generating assumed workflow and deployment inputs…");
            state.scenarioId = currentScenarioId();
            state.generation = await postJson("/api/synthetic/generate", { scenarioId: state.scenarioId });
            state.results = null;
            renderGeneration(state.generation);
            document.getElementById("results-section").classList.add("hidden");
            setBusy(false, "Generated four workflows and four cross-layer graphs. No failure has been executed yet.");
        } catch (error) {
            setBusy(false, error.message, true);
        }
    }

    async function simulate(endpoint, failedNode = null) {
        try {
            if (!state.scenarioId) throw new Error("Generate the synthetic portfolio first.");
            setBusy(true, "Running virtual single-worker-node simulation…");
            const request = { scenarioId: state.scenarioId };
            if (failedNode) request.failedNode = failedNode;
            const payload = await postJson(endpoint, request);
            state.generation = payload;
            state.results = payload.resultArtifact;
            renderGeneration(payload);
            renderResults(state.results);
            setBusy(false, "Simulation complete. The Kubernetes cluster was not changed.");
        } catch (error) {
            setBusy(false, error.message, true);
        }
    }

    function init() {
        document.getElementById("generate-button").addEventListener("click", generate);
        document.getElementById("fail-one-button").addEventListener("click", () => {
            simulate("/api/synthetic/fail-one", document.getElementById("failed-node").value);
        });
        document.getElementById("fail-all-button").addEventListener("click", () => {
            simulate("/api/synthetic/fail-all");
        });
        document.getElementById("download-workflows").addEventListener("click", () => {
            downloadJson("workflow-graphs.json", state.generation.workflowArtifact);
        });
        document.getElementById("download-deployment").addEventListener("click", () => {
            downloadJson("deployment-graph.json", state.generation.deploymentGraph);
        });
        document.getElementById("download-graphs").addEventListener("click", () => {
            downloadJson("cross-layer-graphs.json", state.generation.crossLayerArtifact);
        });
        document.getElementById("download-results").addEventListener("click", () => {
            downloadJson("portfolio-scenario-results.json", state.results);
        });
    }

    global.DisposablePortfolioDemo = { graphSummaries, resultRows, init };
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", init);
    }
})(typeof window !== "undefined" ? window : globalThis);
