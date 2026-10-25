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

    const GRAPH_LAYERS = ["WORKFLOW", "SERVICE", "POD", "NODE", "FAILURE_DOMAIN"];
    const GRAPH_LAYER_LABELS = {
        WORKFLOW: "Workflow",
        SERVICE: "Services",
        POD: "Pods",
        NODE: "Worker nodes",
        FAILURE_DOMAIN: "Failure domains",
    };

    function graphLayout(graph) {
        const width = 1100;
        const nodeWidth = 164;
        const nodeHeight = 42;
        const top = 72;
        const layerGap = 46;
        const left = 28;
        const rowGap = 18;
        const nodesByLayer = Object.fromEntries(GRAPH_LAYERS.map((layer) => [layer, []]));

        (graph?.nodes || []).forEach((node) => {
            if (nodesByLayer[node.nodeType]) nodesByLayer[node.nodeType].push(node);
        });
        GRAPH_LAYERS.forEach((layer) => {
            nodesByLayer[layer].sort((leftNode, rightNode) => (
                String(leftNode.label).localeCompare(String(rightNode.label))
            ));
        });

        const maxRows = Math.max(1, ...GRAPH_LAYERS.map((layer) => nodesByLayer[layer].length));
        const height = Math.max(360, top + (maxRows * (nodeHeight + rowGap)) + 26);
        const positions = {};
        const layers = GRAPH_LAYERS.map((layer, layerIndex) => {
            const x = left + layerIndex * (nodeWidth + layerGap);
            const nodes = nodesByLayer[layer];
            const contentHeight = height - top - 28;
            const usedHeight = nodes.length * nodeHeight + Math.max(0, nodes.length - 1) * rowGap;
            const startY = top + Math.max(0, (contentHeight - usedHeight) / 2);
            const positionedNodes = nodes.map((node, nodeIndex) => {
                const positioned = {
                    ...node,
                    x,
                    y: startY + nodeIndex * (nodeHeight + rowGap),
                    width: nodeWidth,
                    height: nodeHeight,
                };
                positions[node.identifier] = positioned;
                return positioned;
            });
            return {
                type: layer,
                label: GRAPH_LAYER_LABELS[layer],
                x,
                nodes: positionedNodes,
            };
        });

        const edges = (graph?.edges || []).flatMap((edge) => {
            const source = positions[edge.source];
            const target = positions[edge.target];
            if (!source || !target) return [];

            const sourceY = source.y + source.height / 2;
            const targetY = target.y + target.height / 2;
            let path;
            if (source.nodeType === target.nodeType) {
                const sourceX = source.x + source.width / 2;
                const targetX = target.x + target.width / 2;
                const bendX = source.x + source.width + 28;
                path = `M ${sourceX} ${sourceY} C ${bendX} ${sourceY}, ${bendX} ${targetY}, ${targetX} ${targetY}`;
            } else {
                const sourceX = source.x + source.width;
                const targetX = target.x;
                const midpoint = sourceX + (targetX - sourceX) / 2;
                path = `M ${sourceX} ${sourceY} C ${midpoint} ${sourceY}, ${midpoint} ${targetY}, ${targetX} ${targetY}`;
            }
            return [{ ...edge, path }];
        });

        return { width, height, layers, edges };
    }

    function graphClass(value) {
        return String(value || "unknown").toLowerCase().replaceAll("_", "-");
    }

    function shortGraphLabel(value, limit = 21) {
        const label = String(value ?? "");
        return label.length > limit ? `${label.slice(0, limit - 1)}…` : label;
    }

    function renderCrossLayerGraph(graph, graphIndex) {
        const layout = graphLayout(graph);
        const markerId = `graph-arrow-${graphIndex}`;
        const laneWidth = 184;
        const laneHeight = layout.height - 54;

        const lanes = layout.layers.map((layer) => `
            <g class="graph-layer graph-layer--${graphClass(layer.type)}">
                <rect class="graph-layer__background" x="${layer.x - 10}" y="48" width="${laneWidth}" height="${laneHeight}" rx="14"></rect>
                <text class="graph-layer__label" x="${layer.x}" y="31">${escapeHtml(layer.label)}</text>
                <text class="graph-layer__count" x="${layer.x + laneWidth - 30}" y="31">${layer.nodes.length}</text>
            </g>
        `).join("");

        const edges = layout.edges.map((edge) => `
            <path
                class="graph-edge graph-edge--${graphClass(edge.relationship)}"
                d="${edge.path}"
                marker-end="url(#${markerId})"
            ><title>${escapeHtml(edge.source)} → ${escapeHtml(edge.target)} · ${escapeHtml(edge.relationship)}</title></path>
        `).join("");

        const nodes = layout.layers.flatMap((layer) => layer.nodes).map((node) => `
            <g class="graph-node graph-node--${graphClass(node.nodeType)}" transform="translate(${node.x} ${node.y})">
                <title>${escapeHtml(node.nodeType)} · ${escapeHtml(node.label)}</title>
                <rect width="${node.width}" height="${node.height}" rx="10"></rect>
                <circle cx="17" cy="21" r="5"></circle>
                <text x="29" y="26">${escapeHtml(shortGraphLabel(node.label))}</text>
            </g>
        `).join("");

        return `
            <div class="cross-layer-visual">
                <div class="graph-legend" aria-label="Cross-layer graph legend">
                    ${GRAPH_LAYERS.map((layer) => `
                        <span><i class="graph-legend__node graph-legend__node--${graphClass(layer)}"></i>${escapeHtml(GRAPH_LAYER_LABELS[layer])}</span>
                    `).join("")}
                    <span><i class="graph-legend__edge graph-legend__edge--mandatory"></i>Mandatory</span>
                    <span><i class="graph-legend__edge graph-legend__edge--optional"></i>Optional</span>
                    <span><i class="graph-legend__edge graph-legend__edge--calls"></i>Service call</span>
                </div>
                <div class="graph-canvas" tabindex="0" aria-label="Scrollable cross-layer graph for ${escapeHtml(graph.workflowId)}">
                    <svg
                        class="cross-layer-svg"
                        viewBox="0 0 ${layout.width} ${layout.height}"
                        role="img"
                        aria-labelledby="graph-title-${graphIndex} graph-description-${graphIndex}"
                    >
                        <title id="graph-title-${graphIndex}">${escapeHtml(graph.workflowId)} cross-layer graph</title>
                        <desc id="graph-description-${graphIndex}">Workflow dependencies connected to services, pods, worker nodes, and failure domains.</desc>
                        <defs>
                            <marker id="${markerId}" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                                <path d="M 0 0 L 10 5 L 0 10 z"></path>
                            </marker>
                        </defs>
                        ${lanes}
                        <g class="graph-edges">${edges}</g>
                        <g class="graph-nodes">${nodes}</g>
                    </svg>
                </div>
            </div>
        `;
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

    function nodeRiskRanking(resultArtifact) {
        const suppliedRanking = resultArtifact?.results?.[0]?.nodeRiskRanking || [];
        if (suppliedRanking.length) {
            return suppliedRanking.map((entry, index) => ({
                rank: index + 1,
                node: entry.node,
                blastRadius: entry.blastRadius,
                failedWorkflows: [...entry.failedWorkflows],
            }));
        }
        return resultRows(resultArtifact)
            .sort((left, right) => right.blastRadius - left.blastRadius || left.failedNode.localeCompare(right.failedNode))
            .map((entry, index) => ({
                rank: index + 1,
                node: entry.failedNode,
                blastRadius: entry.blastRadius,
                failedWorkflows: [...entry.failedWorkflows],
            }));
    }

    function impactTone(blastRadius) {
        if (blastRadius >= 50) return { key: "high", label: "High impact" };
        if (blastRadius > 0) return { key: "medium", label: "Partial impact" };
        return { key: "none", label: "No workflow failure" };
    }

    function displayWorkflowId(workflowId, scenarioId) {
        const prefix = `synthetic-${scenarioId}-`;
        return String(workflowId).startsWith(prefix) ? String(workflowId).slice(prefix.length) : workflowId;
    }

    function humanizeId(value) {
        return String(value ?? "")
            .replaceAll("_", "-")
            .split("-")
            .filter(Boolean)
            .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
            .join(" ");
    }

    function concentrationStatus(value) {
        const concentration = Number(value) || 0;
        if (concentration >= 1) return { key: "exposed", label: "Single-node exposure" };
        if (concentration > 0.5) return { key: "concentrated", label: "Concentrated" };
        return { key: "distributed", label: "Distributed" };
    }

    function percent(value) {
        return Math.max(0, Math.min(100, Number(value) || 0));
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
            { key: "workflow", label: "Workflows", value: workflows.length, note: "business paths" },
            { key: "service", label: "Services", value: deployment.services.length, note: "deployment targets" },
            { key: "node", label: "Worker nodes", value: deployment.nodes.length, note: "failure candidates" },
            { key: "graph", label: "Cross-layer graphs", value: graphs.workflowCount, note: "complete mappings" },
        ].map((metric) => `
            <article class="portfolio-metric portfolio-metric--${metric.key}">
                <div class="portfolio-metric__icon" aria-hidden="true">${metric.key.charAt(0).toUpperCase()}</div>
                <div>
                    <span>${escapeHtml(metric.label)}</span>
                    <strong>${escapeHtml(metric.value)}</strong>
                    <small>${escapeHtml(metric.note)}</small>
                </div>
            </article>
        `).join("");

        document.getElementById("workflow-list").innerHTML = workflows.map((workflow, index) => {
            const shortId = displayWorkflowId(workflow.workflowId, state.scenarioId);
            const criticality = percent(workflow.criticality * 100);
            return `
                <article class="workflow-card">
                    <header class="workflow-card__header">
                        <span class="workflow-card__number">${String(index + 1).padStart(2, "0")}</span>
                        <span class="criticality-badge" title="Workflow criticality ${escapeHtml(workflow.criticality)}">
                            ${escapeHtml(criticality.toFixed(0))}% criticality
                        </span>
                    </header>
                    <h3 title="${escapeHtml(workflow.workflowId)}">${escapeHtml(humanizeId(shortId))}</h3>
                    <div class="workflow-card__services">
                        <span class="workflow-card__label">Mandatory services</span>
                        <div class="service-chip-list">
                            ${workflow.mandatoryServices.map((service) => `<span class="service-chip service-chip--mandatory">${escapeHtml(service)}</span>`).join("") || '<span class="service-chip service-chip--empty">None</span>'}
                        </div>
                    </div>
                    <div class="workflow-card__services workflow-card__services--optional">
                        <span class="workflow-card__label">Optional services</span>
                        <div class="service-chip-list">
                            ${workflow.optionalServices.map((service) => `<span class="service-chip service-chip--optional">${escapeHtml(service)}</span>`).join("") || '<span class="service-chip service-chip--empty">None</span>'}
                        </div>
                    </div>
                </article>
            `;
        }).join("");

        document.getElementById("deployment-table").innerHTML = `
            <thead><tr><th>Service</th><th>Replica health</th><th>Replica concentration</th><th>Pod → worker node</th></tr></thead>
            <tbody>${deployment.services.map((service) => {
                const concentration = percent(service.replicaConcentration * 100);
                const status = concentrationStatus(service.replicaConcentration);
                const allReady = service.readyReplicas === service.declaredReplicas;
                return `
                    <tr>
                        <td>
                            <div class="service-identity">
                                <span aria-hidden="true">S</span>
                                <div><strong>${escapeHtml(service.name)}</strong><small>Kubernetes service</small></div>
                            </div>
                        </td>
                        <td>
                            <div class="replica-health">
                                <strong>${escapeHtml(service.readyReplicas)} <span>/ ${escapeHtml(service.declaredReplicas)}</span></strong>
                                <span class="readiness-badge readiness-badge--${allReady ? "ready" : "degraded"}">${allReady ? "All ready" : "Degraded"}</span>
                            </div>
                        </td>
                        <td>
                            <div class="concentration concentration--${status.key}">
                                <div><strong>${escapeHtml(concentration.toFixed(0))}%</strong><span>${status.label}</span></div>
                                <div class="concentration__track"><span style="width: ${concentration}%"></span></div>
                            </div>
                        </td>
                        <td>
                            <div class="placement-list">
                                ${service.pods.map((pod) => `
                                    <div class="placement-route">
                                        <span class="placement-route__pod">${escapeHtml(pod.name)}</span>
                                        <span class="placement-route__arrow" aria-hidden="true">→</span>
                                        <span class="placement-route__node">${escapeHtml(pod.node)}</span>
                                    </div>
                                `).join("")}
                            </div>
                        </td>
                    </tr>
                `;
            }).join("")}</tbody>`;

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
                        ${renderCrossLayerGraph(graph, index)}
                        <details class="graph-evidence">
                            <summary>View ${graph.edges.length} relationship records</summary>
                            <div class="table-wrap"><table>
                                <thead><tr><th>Source</th><th>Relationship</th><th>Target</th></tr></thead>
                                <tbody>${graph.edges.map((edge) => `
                                    <tr><td>${escapeHtml(edge.source)}</td><td>${escapeHtml(edge.relationship)}</td><td>${escapeHtml(edge.target)}</td></tr>
                                `).join("")}</tbody>
                            </table></div>
                        </details>
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
        const ranking = nodeRiskRanking(resultArtifact);
        const highestRisk = ranking[0] || { node: "none", blastRadius: 0, failedWorkflows: [] };
        const evaluatedCount = resultArtifact.evaluatedNodes?.length || rows.length;
        const noFailureCount = ranking.filter((entry) => entry.blastRadius === 0).length;
        const comparisonLabel = evaluatedCount > 1
            ? `${evaluatedCount} worker nodes compared independently`
            : `Single-node scenario for ${highestRisk.node}`;

        document.getElementById("result-overview").innerHTML = `
            <div class="result-overview result-overview--${impactTone(highestRisk.blastRadius).key}">
                <div class="result-overview__summary">
                    <div class="result-overview__icon" aria-hidden="true">
                        <svg viewBox="0 0 24 24">
                            <path d="M12 3v6M12 15v6M3 12h6M15 12h6"></path>
                            <circle cx="12" cy="12" r="3"></circle>
                            <circle cx="12" cy="3" r="2"></circle>
                            <circle cx="12" cy="21" r="2"></circle>
                            <circle cx="3" cy="12" r="2"></circle>
                            <circle cx="21" cy="12" r="2"></circle>
                        </svg>
                    </div>
                    <div>
                        <div class="result-overview__labels">
                            <span>Virtual failure complete</span>
                            <span class="result-overview__evidence">Synthetic evidence</span>
                        </div>
                        <h3>${escapeHtml(comparisonLabel)}</h3>
                        <p>
                            Highest predicted impact occurs on <strong>${escapeHtml(highestRisk.node)}</strong>:
                            ${escapeHtml(highestRisk.failedWorkflows.length)} of ${escapeHtml(resultArtifact.totalWorkflows)} workflows fail.
                        </p>
                    </div>
                </div>
                <div class="impact-ring" style="--impact: ${percent(highestRisk.blastRadius)}%" aria-label="Highest blast radius ${escapeHtml(highestRisk.blastRadius.toFixed(1))} percent">
                    <div><strong>${escapeHtml(highestRisk.blastRadius.toFixed(0))}%</strong><span>highest blast radius</span></div>
                </div>
                <dl class="result-overview__metrics">
                    <div><dt>Nodes evaluated</dt><dd>${escapeHtml(evaluatedCount)}</dd></div>
                    <div><dt>Portfolio workflows</dt><dd>${escapeHtml(resultArtifact.totalWorkflows)}</dd></div>
                    <div><dt>Zero-impact nodes</dt><dd>${escapeHtml(noFailureCount)}</dd></div>
                </dl>
            </div>
        `;

        document.getElementById("node-risk-ranking").innerHTML = ranking.map((entry) => {
            const tone = impactTone(entry.blastRadius);
            const workflowNames = entry.failedWorkflows.map((workflowId) => (
                displayWorkflowId(workflowId, resultArtifact.scenarioId)
            ));
            return `
                <article class="risk-row risk-row--${tone.key}">
                    <span class="risk-row__rank">#${entry.rank}</span>
                    <div class="risk-row__identity">
                        <strong>${escapeHtml(entry.node)}</strong>
                        <span>${escapeHtml(workflowNames.join(", ") || "All workflows survive")}</span>
                    </div>
                    <div class="risk-row__bar" role="img" aria-label="${escapeHtml(entry.node)} blast radius ${escapeHtml(entry.blastRadius.toFixed(1))} percent">
                        <span style="width: ${percent(entry.blastRadius)}%"></span>
                    </div>
                    <strong class="risk-row__value">${escapeHtml(entry.blastRadius.toFixed(0))}%</strong>
                    <span class="impact-badge impact-badge--${tone.key}">${tone.label}</span>
                </article>
            `;
        }).join("");

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

        document.getElementById("workflow-outcomes").innerHTML = resultArtifact.results.map((result) => {
            const tone = impactTone(result.blastRadius);
            const unavailableServices = result.failedServices.join(", ") || "None";
            return `
                <article class="node-impact-card node-impact-card--${tone.key}">
                    <header class="node-impact-card__header">
                        <div>
                            <span class="node-impact-card__eyebrow">Assumed failed worker node</span>
                            <h3>${escapeHtml(result.failedNode)}</h3>
                        </div>
                        <span class="impact-badge impact-badge--${tone.key}">${tone.label}</span>
                    </header>

                    <div class="node-impact-card__metrics">
                        <div class="impact-meter impact-meter--danger">
                            <div><span>Blast radius</span><strong>${escapeHtml(result.blastRadius.toFixed(1))}%</strong></div>
                            <div class="impact-meter__track"><span style="width: ${percent(result.blastRadius)}%"></span></div>
                        </div>
                        <div class="impact-meter impact-meter--success">
                            <div><span>Workflow survivability</span><strong>${escapeHtml(result.survivability.toFixed(1))}%</strong></div>
                            <div class="impact-meter__track"><span style="width: ${percent(result.survivability)}%"></span></div>
                        </div>
                    </div>

                    <div class="node-impact-card__facts">
                        <span><strong>${escapeHtml(result.failedWorkflows.length)}</strong> failed workflows</span>
                        <span><strong>${escapeHtml(result.survivingWorkflows.length)}</strong> surviving workflows</span>
                        <span><strong>${escapeHtml(result.failedServices.length)}</strong> unavailable services</span>
                    </div>

                    <div class="unavailable-services">
                        <span>Unavailable after failure</span>
                        <strong>${escapeHtml(unavailableServices)}</strong>
                    </div>

                    <div class="workflow-impact-grid">
                        ${result.workflowImpacts.map((impact) => {
                            const workflowName = displayWorkflowId(impact.workflowId, resultArtifact.scenarioId);
                            if (impact.survives) {
                                return `
                                    <div class="workflow-impact workflow-impact--survives">
                                        <div class="workflow-impact__status" aria-hidden="true">✓</div>
                                        <div>
                                            <strong title="${escapeHtml(impact.workflowId)}">${escapeHtml(workflowName)}</strong>
                                            <span>All mandatory services remain available</span>
                                        </div>
                                        <span class="workflow-impact__verdict">Survives</span>
                                    </div>
                                `;
                            }
                            return `
                                <div class="workflow-impact workflow-impact--fails">
                                    <div class="workflow-impact__status" aria-hidden="true">!</div>
                                    <div>
                                        <strong title="${escapeHtml(impact.workflowId)}">${escapeHtml(workflowName)}</strong>
                                        <div class="failure-path" aria-label="Failure propagation path">
                                            <span>${escapeHtml(result.failedNode)}</span>
                                            <b aria-hidden="true">→</b>
                                            <span>${escapeHtml(impact.failedMandatoryServices.join(", "))} unavailable</span>
                                            <b aria-hidden="true">→</b>
                                            <span>workflow fails</span>
                                        </div>
                                    </div>
                                    <span class="workflow-impact__verdict">Fails</span>
                                </div>
                            `;
                        }).join("")}
                    </div>
                </article>
            `;
        }).join("");
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
            state.generation = await postJson("api/synthetic/generate", { scenarioId: state.scenarioId });
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
            simulate("api/synthetic/fail-one", document.getElementById("failed-node").value);
        });
        document.getElementById("fail-all-button").addEventListener("click", () => {
            simulate("api/synthetic/fail-all");
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

    global.DisposablePortfolioDemo = {
        graphLayout,
        graphSummaries,
        concentrationStatus,
        humanizeId,
        nodeRiskRanking,
        renderCrossLayerGraph,
        resultRows,
        init,
    };
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", init);
    }
})(typeof window !== "undefined" ? window : globalThis);
