/*
 * Member 4 Development Lab — scenario walkthrough and experiment overview.
 *
 * Renders only what the lab API returns. Every value comes from the real Member 4 pipeline; the browser
 * calculates no research value. All text goes through textContent, so no API value is interpreted as markup.
 * Predicted placements are always labelled as offline simulation, never as scheduler decisions.
 */
(function () {
    "use strict";

    const MISSING = "—";

    // ---- helpers ---------------------------------------------------------------------------------------------

    function el(tag, text, className) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        return node;
    }

    function clear(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    function fmt(value, digits) {
        if (value === null || value === undefined || Number.isNaN(Number(value))) return MISSING;
        return Number(value).toFixed(digits === undefined ? 2 : digits);
    }

    function pill(text, kind) {
        return el("span", text, `pill ${kind || "muted"}`);
    }

    function statusPill(status) {
        if (status === "FEASIBLE" || status === "GENERATED" || status === true) return pill(String(status), "ok");
        if (status === "INSUFFICIENT_EVIDENCE") return pill(String(status), "warn");
        return pill(String(status), "bad");
    }

    function isNode(value) {
        return value !== null && typeof value === "object" && typeof value.appendChild === "function";
    }

    function table(headers, rows) {
        const t = el("table");
        const head = t.appendChild(el("thead")).appendChild(el("tr"));
        headers.forEach((h) => head.appendChild(el("th", h)));
        const body = t.appendChild(el("tbody"));
        rows.forEach((cells) => {
            const row = body.appendChild(el("tr"));
            cells.forEach((cell) => {
                const td = el("td");
                if (isNode(cell)) td.appendChild(cell); else td.textContent = cell === null || cell === undefined ? MISSING : String(cell);
                row.appendChild(td);
            });
        });
        return t;
    }

    function kv(label, value) {
        const p = el("p", undefined, "kv");
        p.append(el("b", `${label}: `), document.createTextNode(value === null || value === undefined ? MISSING : String(value)));
        return p;
    }

    async function fetchJson(url) {
        const response = await fetch(url);
        const body = await response.json().catch(() => ({}));
        if (!response.ok) {
            const detail = body && body.detail;
            throw new Error(typeof detail === "string" ? detail : (detail && detail.message) || `Request failed (${response.status})`);
        }
        return body;
    }

    function byRole(role) {
        return document.querySelector(`[data-role="${role}"]`);
    }

    // ---- state -----------------------------------------------------------------------------------------------

    const state = { scenarios: [], current: null, detail: null, weights: null, buttons: new Map() };

    // ---- node/pod diagrams -----------------------------------------------------------------------------------

    function placementDiagram(deployment, failedNode, options) {
        const opts = options || {};
        const wrap = el("div", undefined, "diagram");
        if (!deployment || !deployment.nodes) return wrap;
        const services = opts.onlyServices ? new Set(opts.onlyServices) : null;
        const podsByNode = {};
        deployment.nodes.forEach((n) => { podsByNode[n.name] = []; });
        (deployment.services || []).forEach((service) => {
            if (services && !services.has(service.name)) return;
            (service.pods || []).forEach((pod) => {
                if (podsByNode[pod.node]) podsByNode[pod.node].push({ service: service.name, pod });
            });
        });
        deployment.nodes.forEach((node) => {
            const box = el("div", undefined, `node-box${node.name === failedNode ? " failed" : ""}`);
            box.appendChild(el("div", node.name + (node.name === failedNode ? "  ✕ failed" : ""), "node-name"));
            box.appendChild(el("div",
                `zone ${node.zone || MISSING} · cpu ${fmt(node.cpuAvailable, 1)} · mem ${fmt(node.memoryAvailable, 1)} GiB`,
                "node-meta"));
            podsByNode[node.name].forEach(({ service, pod }) => {
                const added = pod.name.indexOf("-added-") !== -1;
                const lost = node.name === failedNode && opts.markLost;
                const moved = opts.movedPods && opts.movedPods.has(pod.name);
                const cls = added ? " added" : lost ? " lost" : moved ? " moved" : "";
                const suffix = added ? " (predicted added replica)" : moved ? " (predicted move)" : "";
                box.appendChild(el("span", `${service} · ${pod.name}${pod.ready === false ? " (not ready)" : ""}${suffix}`, `pod-chip${cls}`));
            });
            wrap.appendChild(box);
        });
        return wrap;
    }

    // ---- section A: scenario and supplied evidence -----------------------------------------------------------

    function renderInputs(inputs) {
        const body = byRole("inputs-body");
        clear(body);
        body.appendChild(kv("Scenario", inputs.name));
        body.appendChild(kv("Objective", inputs.objective));
        body.appendChild(kv("Situation covered", inputs.situation));
        body.appendChild(kv("Failed worker node (assumed)", inputs.failedNode));

        const wf = inputs.workflow;
        body.appendChild(el("h3", "Workflow"));
        body.appendChild(table(
            ["Workflow", "Criticality", "Mandatory services", "Optional services", "Edges"],
            [[wf.workflowId, fmt(wf.criticality, 2), wf.mandatoryServices.join(", "), wf.optionalServices.join(", ") || MISSING,
              wf.edges.map((e) => `${e.source}→${e.target}`).join(", ")]]));

        body.appendChild(el("h3", `Worker nodes (failure domain: ${inputs.deployment.failureDomainType})`));
        body.appendChild(table(
            ["Node", "Zone", "Ready", "Schedulable", "CPU avail.", "Memory avail. (GiB)", "Taints evidence"],
            inputs.deployment.nodes.map((n) => {
                const taints = inputs.schedulingEvidence.nodeTaints[n.name];
                const taintText = taints === "unread" ? "unread (unknown)"
                    : taints.length ? taints.map((t) => `${t.key}=${t.value}:${t.effect}`).join(", ") : "observed: none";
                return [n.name, n.zone || MISSING, String(n.ready), String(n.schedulable),
                        fmt(n.cpuAvailable, 1), fmt(n.memoryAvailable, 1), taintText];
            })));

        body.appendChild(el("h3", "Services and current replica placement"));
        body.appendChild(table(
            ["Service", "Replicas (ready/declared)", "Concentration", "Nodes", "Pods", "Declared constraints"],
            inputs.deployment.services.map((s) => {
                const intent = s.intent || {};
                const constraints = [
                    intent.nodeSelector && Object.keys(intent.nodeSelector).length ? `nodeSelector ${JSON.stringify(intent.nodeSelector)}` : null,
                    intent.tolerations && intent.tolerations.length ? `${intent.tolerations.length} toleration(s)` : null,
                    intent.topologySpreadConstraints && intent.topologySpreadConstraints.length ? "topology spread configured" : null,
                    intent.podAntiAffinityConfigured ? "pod anti-affinity configured" : null,
                    intent.resourceRequests && intent.resourceRequests.length
                        ? `requests ${intent.resourceRequests.map((r) => `${r.cpu}/${r.memory}`).join(", ")}` : null,
                ].filter(Boolean).join("; ");
                return [s.name, `${s.readyReplicas}/${s.declaredReplicas}`,
                        s.replicaConcentration === null ? MISSING : fmt(s.replicaConcentration, 2),
                        s.hostingNodes.join(", "),
                        s.pods.map((p) => `${p.name}@${p.node}`).join(", "),
                        constraints || "none declared"];
            })));
        body.appendChild(el("p", inputs.schedulingEvidence.note, "section-note"));
    }

    // ---- section B: current risk -----------------------------------------------------------------------------

    function renderRisk(risk, inputs) {
        const body = byRole("risk-body");
        clear(body);
        body.appendChild(table(
            ["Failed node", "Blast radius", "Workflow survivability", "Failed services", "Failed workflows"],
            [[risk.failedNode, `${fmt(risk.blastRadius, 0)}%`, `${fmt(risk.survivability, 0)}%`,
              risk.failedServices.join(", ") || "none", risk.failedWorkflows.join(", ") || "none"]]));
        if (risk.rootCauses && risk.rootCauses.length) {
            body.appendChild(el("h3", "Root causes"));
            body.appendChild(table(
                ["Type", "Service", "Failure domain", "Concentration"],
                risk.rootCauses.map((c) => [c.type, c.service, c.failureDomain, fmt(c.concentration, 2)])));
        }
        if (risk.nodeRiskRanking && risk.nodeRiskRanking.length) {
            body.appendChild(el("h3", "Node risk ranking (blast radius if that node fails)"));
            body.appendChild(table(["Node", "Blast radius"],
                risk.nodeRiskRanking.map((r) => [r.node, `${fmt(r.blastRadius, 0)}%`])));
        }
        const diagram = byRole("risk-diagram");
        clear(diagram);
        diagram.appendChild(placementDiagram(inputs.deployment, risk.failedNode, { markLost: true }));
    }

    // ---- section C: candidates -------------------------------------------------------------------------------

    function renderCandidates(candidates) {
        const body = byRole("candidates-body");
        clear(body);
        if (!candidates.length) {
            body.appendChild(el("p", "The engine generated no candidate change for this scenario. That is not a finding that the workload is resilient: only the modelled change types were considered.", "section-note"));
            return;
        }
        candidates.forEach((c) => {
            const card = el("div", undefined, `candidate-card${c.selected ? " selected" : ""}`);
            const title = el("h3");
            title.append(el("span", `${c.type} → ${c.service}  `), statusPill(c.feasibility.status || (c.feasible ? "FEASIBLE" : "INFEASIBLE")));
            if (c.selected) title.append(document.createTextNode(" "), pill("SELECTED", "ok"));
            card.appendChild(title);
            card.appendChild(kv("Root cause", c.rootCause));
            card.appendChild(kv("Proposed change", c.recommendedChange));
            card.appendChild(kv("Feasibility", c.feasibility.explanation));
            if (c.feasibility.findings && c.feasibility.findings.length) {
                card.appendChild(table(["Constraint", "Status", "Detail", "Node"],
                    c.feasibility.findings.map((f) => [f.constraint, f.status, f.detail, f.node || MISSING])));
            }
            if (c.feasibility.missingEvidence && c.feasibility.missingEvidence.length) {
                card.appendChild(kv("Missing evidence", c.feasibility.missingEvidence.join("; ")));
            }
            card.appendChild(table(
                ["Predicted blast radius after", "Risk reduction", "Resource cost", "Config cost", "Confidence", "Score"],
                [[`${fmt(c.predicted.blastRadiusAfter, 0)}%`, `${fmt(c.predicted.riskReduction, 0)} pts`,
                  c.cost.resource, c.cost.configuration, fmt(c.confidence, 2), fmt(c.score, 4)]]));
            card.appendChild(kv("Confidence meaning", "evidence coverage (observed share of declared replicas), not accuracy"));
            card.appendChild(kv("Approval eligibility", c.approval.ready
                ? "eligible on ranking — but this evaluation is offline, so the real approval gate would still refuse it (provenance)"
                : `not eligible: ${c.approval.reason}`));
            (c.notes || []).forEach((note) => card.appendChild(el("p", note, "section-note")));
            body.appendChild(card);
        });
    }

    // ---- section D: virtual simulation -----------------------------------------------------------------------

    function renderSimulation(detail, feasibility, extras, failedNode) {
        const body = byRole("simulation-body");
        clear(body);
        const simulated = detail.candidates.filter((c) => c.simulatedDeployment);
        if (!simulated.length) {
            body.appendChild(el("p", "No candidate was verified feasible, so no virtual placement exists to show. "
                + "The engine never simulates a change it could not verify.", "section-note"));
            return;
        }
        simulated.forEach((c) => {
            body.appendChild(el("h3", `${c.type} → ${c.service}`));
            body.appendChild(table(
                ["Blast radius before", "Predicted after", "Risk reduction", "Pods to recreate", "Added replicas"],
                [[`${fmt(c.predicted.blastRadiusBefore, 0)}%`, `${fmt(c.predicted.blastRadiusAfter, 0)}%`,
                  `${fmt(c.predicted.riskReduction, 0)} pts`,
                  (c.podsToRecreate || []).join(", ") || "none",
                  (((extras || {})[c.id] || {}).addedReplicas || []).join(", ") || "none"]]));

            const services = Object.keys(c.currentPlacement || {});
            body.appendChild(el("h4", "Current placement (observed input)"));
            body.appendChild(placementDiagram(detail.currentDeployment, failedNode, { onlyServices: services }));
            body.appendChild(el("h4", "Predicted placement after the change (offline simulation — no scheduler ran)"));
            body.appendChild(placementDiagram(c.simulatedDeployment, failedNode, { onlyServices: services }));

            const facts = (feasibility || {})[c.id] || {};
            if (facts.assumptions && facts.assumptions.length) {
                body.appendChild(el("h4", "Assumptions"));
                const list = el("ul");
                facts.assumptions.forEach((a) => list.appendChild(el("li", a)));
                body.appendChild(list);
            }
            if (facts.notModeled && facts.notModeled.length) {
                body.appendChild(el("h4", "Not modelled (uncertainty)"));
                const list = el("ul");
                facts.notModeled.forEach((n) => list.appendChild(el("li", n)));
                body.appendChild(list);
            }
        });
    }

    // ---- section E: ranking ----------------------------------------------------------------------------------

    function renderRanking(ranking, detail) {
        const body = byRole("ranking-body");
        clear(body);
        body.appendChild(kv("Formula version", ranking.formulaVersion));
        body.appendChild(kv("Weights", `target share ${ranking.weights.targetShare}, disruption discount ${ranking.weights.disruptionDiscount}, resource discount ${ranking.weights.resourceDiscount} (${ranking.weights.status})`));
        body.appendChild(kv("Score formula", "confidence × benefit × (1 − d·churn) × (1 − r·resource), benefit = α·target reduction + (1−α)·cluster reduction"));
        body.appendChild(kv("Placement churn", ranking.placementChurnMeaning));

        const entries = Object.values(ranking.candidates || {})
            .sort((a, b) => (a.rank === null ? 999 : a.rank) - (b.rank === null ? 999 : b.rank));
        body.appendChild(table(
            ["Rank", "Candidate", "Score", "Benefit", "Churn factor", "Resource factor", "Confidence", "Excluded"],
            entries.map((e) => [
                e.rank === null ? "not ranked" : e.rank,
                `${e.type} → ${e.service}`,
                e.score === null ? MISSING : fmt(e.score, 4),
                e.components ? fmt(e.components.benefit, 3) : MISSING,
                e.components ? fmt(e.components.churnFactor, 3) : MISSING,
                e.components ? fmt(e.components.resourceFactor, 3) : MISSING,
                e.components ? fmt(e.components.confidence, 2) : MISSING,
                e.excluded || "—"])));

        body.appendChild(el("h3", "Why the selection is what it is"));
        const list = el("ul");
        detail.candidates.forEach((c) => list.appendChild(el("li", `${c.id}: ${c.selectionJustification}`)));
        body.appendChild(list);
        body.appendChild(kv("Outcome", `${detail.outcome.state} — ${detail.outcome.message}`));
    }

    // ---- section F: YAML -------------------------------------------------------------------------------------

    function renderYaml(yaml) {
        const status = byRole("yaml-status");
        const panel = byRole("yaml-panel");
        clear(status);
        panel.hidden = true;
        if (!yaml.available) {
            status.appendChild(el("p", yaml.reason, "section-note"));
            return;
        }
        const line = el("p");
        line.append(el("span", `Proposal for ${yaml.recommendationId} · sha256 ${yaml.sha256.slice(0, 12)}… · eligible YAML, but `),
            pill("applicable: false", "warn"));
        status.appendChild(line);
        status.appendChild(el("p", "This proposal comes from an offline-fixture evaluation, so the engine marks it not applicable: only a live-discovery evaluation can produce YAML a developer may approve. " + (yaml.reason || ""), "section-note"));
        (yaml.disclosures || []).forEach((d) => status.appendChild(el("p", d, "section-note")));
        if (yaml.podReplacement) status.appendChild(el("p", yaml.podReplacement.note, "section-note"));
        byRole("yaml-text").textContent = yaml.text;
        panel.hidden = false;
    }

    // ---- section G: offline validation -----------------------------------------------------------------------

    function renderValidation(validation) {
        const body = byRole("validation-body");
        clear(body);
        if (!validation.available) {
            body.appendChild(el("p", validation.reason, "section-note"));
            return;
        }
        body.appendChild(el("p", validation.label, "section-note"));
        body.appendChild(el("p", validation.approvalGate, "section-note"));
        body.appendChild(kv("Validated recommendation", validation.recommendationId));
        body.appendChild(kv("Baseline", validation.baseline));

        Object.entries(validation.runs).forEach(([key, run]) => {
            const title = key === "schedulerSpreads"
                ? "Scripted scheduler honours the constraint (spread)"
                : "Scripted scheduler changes nothing (stay)";
            body.appendChild(el("h3", title));
            const head = el("p");
            head.append(el("span", `state ${run.state} · `), run.validated ? pill("VALIDATED", "ok") : pill("NOT VALIDATED", "bad"),
                el("span", ` · prediction error ${fmt(run.predictionError, 2)} pts`));
            body.appendChild(head);
            (run.reasons || []).forEach((r) => body.appendChild(el("p", `✗ ${r}`, "section-note")));

            const cmp = run.comparison || {};
            if (cmp.blastRadius) {
                body.appendChild(table(
                    ["", "Before", "After"],
                    [["Predicted", `${fmt(cmp.blastRadius.predictedBefore, 0)}%`, `${fmt(cmp.blastRadius.predictedAfter, 0)}%`],
                     ["Observed (scripted)", `${fmt(cmp.blastRadius.observedBefore, 0)}%`, `${fmt(cmp.blastRadius.observedAfter, 0)}%`]]));
            }
            if (cmp.riskReduction) {
                body.appendChild(kv("Risk reduction", `predicted ${fmt(cmp.riskReduction.predicted, 1)} pts, observed ${fmt(cmp.riskReduction.observed, 1)} pts (${cmp.riskReduction.observedBasis})`));
                if (cmp.riskReduction.caveat) body.appendChild(el("p", cmp.riskReduction.caveat, "section-note"));
            }
            if (cmp.serviceSurvival) {
                body.appendChild(kv("Service survival agreement", cmp.serviceSurvival.agreement));
                body.appendChild(table(["Service", "Predicted survives", "Observed survives", "Match"],
                    cmp.serviceSurvival.services.map((s) => [s.service, String(s.predictedSurvives), String(s.observedSurvives), s.match ? "✓" : "✗"])));
            }
            if (cmp.mandatoryWorkflowSurvival) {
                const w = cmp.mandatoryWorkflowSurvival;
                body.appendChild(kv("Mandatory workflow survival", `predicted ${w.predictedSurvives}, observed ${w.observedSurvives} — ${w.match ? "match" : "MISMATCH"}`));
            }
        });
    }

    // ---- experiment overview (task 3) ------------------------------------------------------------------------

    function renderExperiment(data) {
        const body = byRole("experiment-body");
        clear(body);
        body.appendChild(el("p", data.label, "section-note"));
        body.appendChild(el("p", data.demonstrates, "section-note"));

        body.appendChild(el("h3", "Method comparison (baselines)"));
        body.appendChild(table(["Method", "Evaluated", "Why / why not"],
            data.baselines.map((b) => [b.name, b.evaluated ? "yes" : "not evaluated", b.reason])));

        const m = data.metrics;
        body.appendChild(el("h3", "Aggregate metrics"));
        body.appendChild(table(
            ["Scenarios", "With a selected recommendation", "With generated YAML", "Validated when scheduler spreads", "Prediction errors (pts, scheduler spreads)"],
            [[m.scenarios, m.withSelectedRecommendation, m.withGeneratedYaml, m.validatedWhenSchedulerSpreads,
              m.predictionErrorsWhenSchedulerSpreads.join(", ") || MISSING]]));

        body.appendChild(el("h3", "Scenario matrix"));
        body.appendChild(table(
            ["Scenario", "Relevant inputs", "Detected problem", "Candidate recommendations", "Selected recommendation", "Action / validation", "Expected result"],
            data.rows.map((row) => {
                const problem = row.detectedProblem
                    ? `${row.detectedProblem.failedServices.join(", ") || "no service"} fail; blast ${fmt(row.detectedProblem.blastRadius, 0)}% (${(row.detectedProblem.rootCauses || []).map((c) => c.type).join(", ")})`
                    : "no failure under the assumed node loss";
                const cands = row.candidates.length
                    ? row.candidates.map((c) => `${c.type}→${c.service} (${c.feasible ? "feasible" : "refused"}, Δ${fmt(c.riskReduction, 0)})`).join("; ")
                    : "none generated";
                const validation = row.validation
                    ? `spread: ${row.validation.schedulerSpreads.validated ? "validated" : "not validated"} (err ${fmt(row.validation.schedulerSpreads.predictionError, 1)}); no-change: ${row.validation.schedulerChangesNothing.validated ? "validated" : "not validated"} (err ${fmt(row.validation.schedulerChangesNothing.predictionError, 1)})`
                    : "no applicable proposal — nothing validated";
                const expected = `${row.expected.strategy} ${row.expected.feasibility || "no candidate"}; yaml ${row.expected.yaml}${row.expected.selected ? "; selected" : ""}`;
                return [row.scenario,
                        `${row.inputs.service || ""} ${row.inputs.replicas || ""} on ${(row.inputs.placement || []).join(",")}`,
                        problem, cands, row.selected || "none", validation, expected];
            })));
    }

    // ---- orchestration ---------------------------------------------------------------------------------------

    function showError(message) {
        const placeholder = byRole("placeholder");
        placeholder.hidden = false;
        clear(placeholder);
        placeholder.appendChild(el("p", message, "error-box"));
        byRole("scenario-view").hidden = true;
    }

    function weightQuery() {
        if (!state.weights) return "";
        const params = new URLSearchParams();
        ["targetShare", "disruptionDiscount", "resourceDiscount"].forEach((key) => {
            const snake = key.replace(/[A-Z]/g, (ch) => `_${ch.toLowerCase()}`);
            if (state.weights[key] !== null && state.weights[key] !== undefined) params.set(snake, state.weights[key]);
        });
        const text = params.toString();
        return text ? `?${text}` : "";
    }

    async function selectScenario(name) {
        try {
            const detail = await fetchJson(`/api/dev-lab/scenarios/${name}${weightQuery()}`);
            state.current = name;
            state.detail = detail;
            renderInputs(detail.inputs);
            renderRisk(detail.currentRisk, detail.inputs);
            renderCandidates(detail.detail.candidates);
            renderSimulation(detail.detail, detail.feasibility, detail.extras, detail.currentRisk.failedNode);
            renderRanking(detail.ranking, detail.detail);
            renderYaml(detail.detail.yaml);
            renderValidation(detail.validation);
            const w = detail.ranking.weights;
            byRole("weight-target-share").value = w.targetShare;
            byRole("weight-disruption-discount").value = w.disruptionDiscount;
            byRole("weight-resource-discount").value = w.resourceDiscount;
            byRole("placeholder").hidden = true;
            byRole("scenario-view").hidden = false;
            state.buttons.forEach((button, scenarioName) => {
                button.classList.toggle("active", scenarioName === name);
            });
        } catch (error) {
            showError(error.message);
        }
    }

    function renderScenarioList(scenarios) {
        const list = byRole("scenario-list");
        clear(list);
        state.buttons = new Map();
        scenarios.forEach((scenario) => {
            const item = el("li");
            const button = el("button");
            button.appendChild(el("span", scenario.name, "scenario-name"));
            button.appendChild(el("span", scenario.situation, "scenario-situation"));
            button.addEventListener("click", () => selectScenario(scenario.name));
            state.buttons.set(scenario.name, button);
            item.appendChild(button);
            list.appendChild(item);
        });
    }

    async function loadExperiment() {
        const body = byRole("experiment-body");
        clear(body);
        body.appendChild(el("p", "Evaluating all scenarios…", "section-note"));
        const data = await fetchJson("/api/dev-lab/experiment");
        renderExperiment(data);
    }

    function switchView(which) {
        byRole("scenario-panel").style.display = which === "scenarios" ? "" : "none";
        byRole("scenario-view").hidden = which !== "scenarios" || !state.detail;
        byRole("placeholder").hidden = which !== "scenarios" || !!state.detail;
        byRole("experiment-view").hidden = which !== "experiment";
        byRole("view-scenarios").classList.toggle("active", which === "scenarios");
        byRole("view-experiment").classList.toggle("active", which === "experiment");
    }

    async function start() {
        byRole("view-scenarios").addEventListener("click", () => switchView("scenarios"));
        byRole("view-experiment").addEventListener("click", () => { switchView("experiment"); loadExperiment().catch((e) => showError(e.message)); });
        byRole("apply-weights").addEventListener("click", () => {
            state.weights = {
                targetShare: parseFloat(byRole("weight-target-share").value),
                disruptionDiscount: parseFloat(byRole("weight-disruption-discount").value),
                resourceDiscount: parseFloat(byRole("weight-resource-discount").value),
            };
            if (state.current) selectScenario(state.current);
        });
        byRole("reset-weights").addEventListener("click", () => {
            state.weights = null;
            if (state.current) selectScenario(state.current);
        });
        byRole("yaml-copy").addEventListener("click", async () => {
            const text = byRole("yaml-text").textContent;
            const clipboard = typeof navigator !== "undefined" && navigator ? navigator.clipboard : null;
            try {
                if (!clipboard) throw new Error("clipboard unavailable");
                await clipboard.writeText(text);
                byRole("yaml-copied").hidden = false;
            } catch (error) {
                byRole("yaml-copied").hidden = true;
            }
        });
        const data = await fetchJson("/api/dev-lab/scenarios");
        state.scenarios = data.scenarios;
        renderScenarioList(data.scenarios);
    }

    window.DevLab = { renderScenarioList, renderExperiment, selectScenario };

    document.addEventListener("DOMContentLoaded", () => {
        start().catch((error) => showError(error.message));
    });
})();
