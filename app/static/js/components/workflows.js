/*
 * Workflow Dependency Modeling page script.
 * Owned by: Member 1 (A.D. Perera).
 *
 * Supports both sample data and live Jaeger trace ingestion & automated discovery.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { setError, formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    // Live configuration controls
    const jaegerUrlInput = document.querySelector('[data-role="jaeger-url-input"]');
    const entryServiceInput = document.querySelector('[data-role="entry-service-input"]');
    const lookbackSelect = document.querySelector('[data-role="lookback-select"]');
    const jaegerStatusBadge = document.querySelector('[data-role="jaeger-status-badge"]');
    const dataSourceBadge = document.querySelector('[data-role="data-source-badge"]');

    const discoverButton = document.querySelector('[data-role="discover-button"]');
    const reloadSampleButton = document.querySelector('[data-role="reload-sample-button"]');
    const extractLiveButton = document.querySelector('[data-role="extract-live-button"]');
    const discoveredContainer = document.querySelector('[data-role="discovered-workflows-container"]');
    const discoveredSelect = document.querySelector('[data-role="discovered-workflow-select"]');
    const useCandidateButton = document.querySelector('[data-role="use-candidate-button"]');
    const scenarioForm = document.querySelector('[data-role="scenario-form"]');
    const scenarioProtocol = document.querySelector('[data-role="scenario-protocol"]');
    const scenarioStatus = document.querySelector('[data-role="scenario-form-status"]');
    const scenarioList = document.querySelector('[data-role="scenario-registry-list"]');
    const refreshScenariosButton = document.querySelector('[data-role="refresh-scenarios-button"]');
    const executionActor = document.querySelector('[data-role="execution-actor"]');
    const executionBatchId = document.querySelector('[data-role="execution-batch-id"]');
    const executionStatus = document.querySelector('[data-role="execution-runner-status"]');
    const executionList = document.querySelector('[data-role="execution-ledger-list"]');
    const runBenchmarkBatchButton = document.querySelector('[data-role="run-benchmark-batch-button"]');
    const refreshExecutionsButton = document.querySelector('[data-role="refresh-executions-button"]');
    const assessmentForm = document.querySelector('[data-role="assessment-form"]');
    const assessmentStatus = document.querySelector('[data-role="assessment-status"]');
    const assessmentList = document.querySelector('[data-role="dependency-assessment-list"]');
    const refreshAssessmentsButton = document.querySelector('[data-role="refresh-assessments-button"]');
    const approvalActor = document.querySelector('[data-role="assessment-approval-actor"]');
    const approvalRationale = document.querySelector('[data-role="assessment-approval-rationale"]');
    const referenceForm = document.querySelector('[data-role="dependency-reference-form"]');
    const referenceStatus = document.querySelector('[data-role="dependency-reference-status"]');
    const experimentStatus = document.querySelector('[data-role="classification-experiment-status"]');
    const classificationResults = document.querySelector('[data-role="classification-lab-results"]');
    const refreshClassificationLab = document.querySelector('[data-role="refresh-classification-lab"]');
    const runClassificationEvaluation = document.querySelector('[data-role="run-classification-evaluation"]');
    const runPolicySensitivity = document.querySelector('[data-role="run-policy-sensitivity"]');
    const runRepeatability = document.querySelector('[data-role="run-repeatability"]');
    const stageButtons = document.querySelectorAll('[data-workflow-stage-button]');
    const stagePanels = document.querySelectorAll('[data-workflow-stage]');
    const guidedForm = document.querySelector('[data-role="guided-analysis-form"]');
    const guidedStatus = document.querySelector('[data-role="guided-analysis-status"]');
    const guidedDiscovery = document.querySelector('[data-role="guided-discovery"]');
    const guidedOperation = document.querySelector('[data-role="guided-operation"]');
    const guidedInspectButton = document.querySelector('[data-role="guided-inspect-button"]');
    const guidedResult = document.querySelector('[data-role="guided-result"]');
    const researchAudit = document.querySelector(".research-audit");

    let discoveredWorkflows = [];
    let guidedWorkflows = [];
    let liveMode = false;
    let renderVersion = 0;
    let researchAuditInitialized = false;

    function activateWorkflowStage(stageName) {
        stagePanels.forEach((panel) => {
            panel.hidden = panel.dataset.workflowStage !== stageName;
        });
        stageButtons.forEach((button) => {
            const active = button.dataset.workflowStageButton === stageName;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", String(active));
        });
    }

    function setScenarioStatus(message, isError = false) {
        if (!scenarioStatus) return;
        scenarioStatus.textContent = message;
        scenarioStatus.style.color = isError ? "var(--color-danger, #b91c1c)" : "var(--color-text-muted)";
    }

    async function requestScenario(endpoint, options = {}) {
        const response = await fetch(endpoint, {
            ...options,
            headers: {
                Accept: "application/json",
                ...(options.headers || {}),
            },
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(data.detail || `Request failed with HTTP ${response.status}`);
        }
        return data;
    }

    function guidedConnectionPayload() {
        const values = new FormData(guidedForm);
        return {
            environmentId: String(values.get("environmentId")).trim(),
            evidenceTier: String(values.get("evidenceTier")),
            jaegerUrl: String(values.get("jaegerUrl")).trim(),
            entryService: String(values.get("entryService")).trim(),
            lookback: String(values.get("lookback")),
        };
    }

    function setGuidedStatus(message, isError = false) {
        if (!guidedStatus) return;
        guidedStatus.textContent = message;
        guidedStatus.style.color = isError
            ? "var(--color-danger, #b91c1c)"
            : "var(--color-text-muted)";
    }

    function guidedConnectionError(error) {
        const message = String(error && error.message ? error.message : error);
        if (message.includes("Could not fetch traces") || message.includes("Jaeger")) {
            return "Jaeger is not reachable. Start the Online Boutique experiment stack or verify the approved Jaeger URL, then try again.";
        }
        return message;
    }

    function guidedText(tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value;
        return element;
    }

    function updateExperimentPath(activeIndex) {
        document.querySelectorAll(".experiment-path li").forEach((item, index) => {
            item.classList.toggle("is-current", index === activeIndex);
            item.classList.toggle("is-complete", index < activeIndex);
        });
    }

    function renderCampaign(campaign) {
        if (!guidedResult) return;
        guidedResult.hidden = false;
        guidedResult.replaceChildren();
        updateExperimentPath(campaign.status === "baseline_required" ? 1 : 2);

        const heading = document.createElement("div");
        heading.className = "guided-result__heading";
        const identity = document.createElement("div");
        identity.append(
            guidedText("span", "guided-result__label", "Controlled experiment plan"),
            guidedText("h4", "", campaign.workflowName),
            guidedText("p", "", `${campaign.environmentId} · ${campaign.evidenceTier} evidence`)
        );
        heading.append(
            identity,
            guidedText("span", "guided-maturity", campaign.status.replaceAll("_", " "))
        );

        const metrics = document.createElement("div");
        metrics.className = "guided-result__metrics";
        metrics.append(
            resultCard("Baseline gate", `${campaign.baseline.requiredTrials} trials`, "All traces must correlate exactly"),
            resultCard("Candidates", String(campaign.candidateServices.length), "One intervention cohort per service"),
            resultCard("Observed evidence", `${campaign.observedTraceCount} traces`, "Discovery only—not dependency proof")
        );

        const contract = document.createElement("section");
        contract.className = "guided-contract guided-contract--confirmed";
        contract.append(
            guidedText("span", "guided-result__label", "Locked success contract"),
            guidedText("h5", "", campaign.successContract.outcome),
            guidedText(
                "p",
                "",
                `${campaign.successContract.terminalService} · ${campaign.successContract.terminalOperation} · HTTP ${campaign.successContract.acceptedStatusCodes.join(", ")}`
            )
        );

        const runbook = document.createElement("section");
        runbook.className = "campaign-runbook";
        runbook.appendChild(guidedText("h5", "", "Intervention runbook"));
        const list = document.createElement("div");
        list.className = "campaign-runbook__list";
        campaign.interventions.forEach((intervention, index) => {
            const item = document.createElement("article");
            item.className = "campaign-runbook__item";
            item.append(
                guidedText("span", "campaign-runbook__number", String(index + 1).padStart(2, "0")),
                guidedText("strong", "", intervention.targetService),
                guidedText("small", "", "Make unavailable · confirm · collect cohort · restore")
            );
            list.appendChild(item);
        });
        runbook.appendChild(list);

        const next = document.createElement("div");
        next.className = "guided-next";
        next.appendChild(guidedText("h5", "", "Why classification is blocked"));
        const blockers = document.createElement("ol");
        campaign.blockers.forEach((blocker) => blockers.appendChild(guidedText("li", "", blocker)));
        next.appendChild(blockers);

        guidedResult.append(heading, metrics, contract, runbook, next);
        if (dataSourceBadge) {
            dataSourceBadge.textContent = "EXPERIMENT · BASELINE REQUIRED";
            dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
        }
    }

    async function createGuidedCampaign(sessionId, requiredTrials, button, status) {
        button.disabled = true;
        status.textContent = "Generating baseline and intervention cohorts…";
        try {
            const campaign = await requestScenario("/api/workflows/experiment-campaigns", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Actor": "guided-local-researcher",
                },
                body: JSON.stringify({
                    analysisSessionId: sessionId,
                    successContractConfirmed: true,
                    requiredBaselineTrials: requiredTrials,
                }),
            });
            renderCampaign(campaign);
            setGuidedStatus("Experiment planned. Baseline evidence is now required.");
        } catch (error) {
            status.textContent = error.message;
            status.classList.add("is-error");
            button.disabled = false;
        }
    }

    function renderGuidedSession(session) {
        if (!guidedResult) return;
        guidedResult.hidden = false;
        guidedResult.replaceChildren();

        const heading = document.createElement("div");
        heading.className = "guided-result__heading";
        const identity = document.createElement("div");
        identity.append(
            guidedText("span", "guided-result__label", "Observed workflow"),
            guidedText("h4", "", session.operationName),
            guidedText(
                "p",
                "",
                `${session.environmentId} · ${session.evidenceTier} evidence · ${session.lookback} window`
            )
        );
        const maturity = guidedText("span", "guided-maturity", session.maturity);
        heading.append(identity, maturity);

        const metrics = document.createElement("div");
        metrics.className = "guided-result__metrics";
        metrics.append(
            resultCard("Traces", String(session.traceCount), "Matched to this operation"),
            resultCard("Services", String(session.observedServices.length), "Observed participants"),
            resultCard("Edges", String(session.edges.length), "Observed call relationships")
        );

        const warning = document.createElement("div");
        warning.className = "guided-result__warning";
        warning.append(
            guidedText("strong", "", "Observed does not mean validated."),
            guidedText("span", "", session.limitations.join(" "))
        );

        const evidence = document.createElement("div");
        evidence.className = "guided-evidence";
        evidence.appendChild(guidedText("h5", "", "Observed service participation"));
        session.serviceEvidence.forEach((item) => {
            const row = document.createElement("div");
            row.className = "guided-evidence__row";
            const service = guidedText("strong", "", item.service);
            const track = document.createElement("span");
            track.className = "guided-evidence__track";
            const bar = document.createElement("span");
            bar.style.width = `${Math.round(item.frequency * 100)}%`;
            track.appendChild(bar);
            const value = guidedText(
                "small",
                "",
                `${item.traceCount}/${session.traceCount} traces · ${Math.round(item.frequency * 100)}%`
            );
            row.append(service, track, value);
            evidence.appendChild(row);
        });

        const contract = document.createElement("section");
        contract.className = "guided-contract";
        contract.append(
            guidedText("span", "guided-result__label", "Business success contract"),
            guidedText("h5", "", "Customer receives a confirmed order"),
            guidedText("p", "", "The checkout request completes with HTTP 200 and the checkoutservice records PlaceOrder.")
        );
        const confirmation = document.createElement("label");
        confirmation.className = "guided-contract__confirmation";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        confirmation.append(checkbox, document.createTextNode(" This correctly defines a successful checkout for this experiment."));
        const controls = document.createElement("div");
        controls.className = "guided-contract__controls";
        const trialsLabel = guidedText("label", "", "Baseline trials");
        const trials = document.createElement("input");
        trials.type = "number";
        trials.min = "3";
        trials.max = "30";
        trials.value = "5";
        trialsLabel.appendChild(trials);
        const planButton = guidedText("button", "experiment-button", "Create controlled experiment");
        planButton.type = "button";
        planButton.disabled = true;
        const planStatus = guidedText("small", "guided-contract__status", "No service will be changed by this action.");
        checkbox.addEventListener("change", () => {
            planButton.disabled = !checkbox.checked;
        });
        planButton.addEventListener("click", () => {
            createGuidedCampaign(session.sessionId, Number(trials.value), planButton, planStatus);
        });
        controls.append(trialsLabel, planButton, planStatus);
        contract.append(confirmation, controls);

        guidedResult.append(heading, metrics, warning, evidence, contract);
        updateExperimentPath(0);
        if (dataSourceBadge) {
            dataSourceBadge.textContent = `${session.evidenceTier.toUpperCase()} · OBSERVED`;
            dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
        }
    }

    async function discoverGuidedWorkflows(event) {
        event.preventDefault();
        if (!guidedForm || !guidedForm.reportValidity()) return;
        const button = guidedForm.querySelector('[data-role="guided-discover-button"]');
        button.disabled = true;
        setGuidedStatus("Checking the approved Jaeger connection…");
        try {
            const payload = guidedConnectionPayload();
            const discovered = await requestScenario(
                "/api/workflows/analysis-sessions/discover",
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                }
            );
            guidedWorkflows = discovered.filter((workflow) =>
                workflow.operationName.toLowerCase().includes("checkout")
            );
            guidedOperation.replaceChildren();
            guidedWorkflows.forEach((workflow) => {
                const option = document.createElement("option");
                option.value = workflow.operationName;
                option.textContent = `${workflow.operationName} · ${workflow.traceCount} traces · ${workflow.participatingServices.length} services`;
                guidedOperation.appendChild(option);
            });
            guidedDiscovery.hidden = guidedWorkflows.length === 0;
            setGuidedStatus(
                guidedWorkflows.length
                    ? `Found ${guidedWorkflows.length} checkout operation${guidedWorkflows.length === 1 ? "" : "s"}. Select the one representing the full customer checkout.`
                    : "Jaeger is reachable, but no checkout operation was found in this trace window.",
                guidedWorkflows.length === 0
            );
        } catch (error) {
            guidedDiscovery.hidden = true;
            setGuidedStatus(guidedConnectionError(error), true);
        } finally {
            button.disabled = false;
        }
    }

    async function inspectGuidedWorkflow() {
        if (!guidedOperation || !guidedOperation.value) return;
        guidedInspectButton.disabled = true;
        setGuidedStatus("Creating an immutable observational evidence record…");
        try {
            const session = await requestScenario("/api/workflows/analysis-sessions", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Actor": "guided-local-operator",
                },
                body: JSON.stringify({
                    ...guidedConnectionPayload(),
                    operationName: guidedOperation.value,
                }),
            });
            renderGuidedSession(session);
            setGuidedStatus("Observation recorded. No dependency-necessity claim has been made.");
        } catch (error) {
            setGuidedStatus(error.message, true);
        } finally {
            guidedInspectButton.disabled = false;
        }
    }

    async function loadLatestExperiment() {
        if (!guidedResult) return;
        try {
            const campaigns = await requestScenario("/api/workflows/experiment-campaigns");
            if (campaigns.length) {
                renderCampaign(campaigns[0]);
                return;
            }
            const sessions = await requestScenario("/api/workflows/analysis-sessions");
            if (sessions.length) renderGuidedSession(sessions[0]);
        } catch (error) {
            setGuidedStatus(`Previous analysis unavailable: ${error.message}`, true);
        }
    }

    function setLabStatus(target, message, isError = false) {
        if (!target) return;
        target.textContent = message;
        target.style.color = isError
            ? "var(--color-danger, #b91c1c)"
            : "var(--color-text-muted)";
    }

    function resultCard(label, value, detail) {
        const card = document.createElement("article");
        card.className = "classification-result";
        const title = document.createElement("strong");
        const metric = document.createElement("span");
        const hint = document.createElement("small");
        title.textContent = label;
        metric.textContent = value;
        hint.textContent = detail;
        card.append(title, metric, hint);
        return card;
    }

    function renderClassificationEvaluation(evaluation) {
        if (!classificationResults) return;
        classificationResults.replaceChildren(
            resultCard("Macro F1", evaluation.macroF1.toFixed(3), "Equal weight per class"),
            resultCard("Micro F1", evaluation.microF1.toFixed(3), "Aggregate classified decisions"),
            resultCard("Coverage", formatRatioAsPercentage(evaluation.coverage), "Reference services classified"),
            resultCard("Abstention", formatRatioAsPercentage(evaluation.abstentionRate), "Insufficient or missing evidence"),
            resultCard("Accuracy", formatRatioAsPercentage(evaluation.accuracy), "Among classified services"),
            resultCard(
                "Reference",
                `v${evaluation.referenceVersion}`,
                evaluation.synthetic ? "Synthetic engineering reference" : "Independent reviewed reference"
            )
        );
    }

    async function loadClassificationLab() {
        if (!classificationResults) return;
        try {
            const evaluations = await requestScenario("/api/workflows/dependency-evaluations");
            if (!evaluations.length) {
                classificationResults.innerHTML = '<p class="muted">No Phase 5 classification evaluation has been recorded yet.</p>';
                return;
            }
            renderClassificationEvaluation(evaluations.at(-1));
        } catch (error) {
            classificationResults.textContent = `Classification results unavailable: ${error.message}`;
        }
    }

    async function saveDependencyReference(event) {
        event.preventDefault();
        if (!referenceForm || !referenceForm.reportValidity()) return;
        const values = new FormData(referenceForm);
        const actor = String(values.get("actor")).trim();
        const expectedVersion = Number(values.get("expectedVersion"));
        const button = referenceForm.querySelector('button[type="submit"]');
        button.disabled = true;
        setLabStatus(referenceStatus, "Saving immutable reference version…");
        try {
            const payload = {
                workflowId: String(values.get("workflowId")).trim(),
                scenarioVersion: Number(values.get("scenarioVersion")),
                labels: JSON.parse(String(values.get("labels"))),
                source: String(values.get("source")).trim(),
                synthetic: values.get("synthetic") === "on",
            };
            const reference = await requestScenario(
                `/api/workflows/dependency-references?expected_version=${expectedVersion}`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-Actor": actor },
                    body: JSON.stringify(payload),
                }
            );
            referenceForm.elements.expectedVersion.value = reference.referenceVersion;
            setLabStatus(referenceStatus, `Saved reference version ${reference.referenceVersion}.`);
        } catch (error) {
            setLabStatus(referenceStatus, error.message, true);
        } finally {
            button.disabled = false;
        }
    }

    function evaluationInputs() {
        return {
            assessmentId: document.querySelector('[data-role="evaluation-assessment-id"]').value.trim(),
            evaluationId: document.querySelector('[data-role="classification-evaluation-id"]').value.trim(),
            referenceVersion: Number(document.querySelector('[data-role="evaluation-reference-version"]').value),
            actor: document.querySelector('[data-role="classification-evaluation-actor"]').value.trim(),
        };
    }

    async function evaluateClassification() {
        const values = evaluationInputs();
        if (!values.assessmentId || !values.evaluationId || !values.actor) {
            setLabStatus(experimentStatus, "Assessment ID, evaluation ID, and evaluator are required.", true);
            return;
        }
        runClassificationEvaluation.disabled = true;
        setLabStatus(experimentStatus, "Evaluating frozen classifications…");
        try {
            const evaluation = await requestScenario("/api/workflows/dependency-evaluations", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Actor": values.actor },
                body: JSON.stringify({
                    evaluationId: values.evaluationId,
                    assessmentId: values.assessmentId,
                    referenceVersion: values.referenceVersion,
                }),
            });
            renderClassificationEvaluation(evaluation);
            setLabStatus(experimentStatus, "Evaluation recorded. Reference truth was loaded after the frozen assessment.");
        } catch (error) {
            setLabStatus(experimentStatus, error.message, true);
        } finally {
            runClassificationEvaluation.disabled = false;
        }
    }

    async function evaluatePolicySensitivity() {
        const { assessmentId } = evaluationInputs();
        if (!assessmentId) {
            setLabStatus(experimentStatus, "A primary assessment ID is required.", true);
            return;
        }
        runPolicySensitivity.disabled = true;
        setLabStatus(experimentStatus, "Reclassifying frozen evidence under policy variants…");
        try {
            const report = await requestScenario("/api/workflows/dependency-experiments/sensitivity", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    assessmentId,
                    cases: [
                        { caseId: "strict", policy: { minSamplesPerCohort: 5, optionalEffectTolerance: 0.05, mandatoryEffectThreshold: 0.6, maxMandatorySuccessRate: 0.1 } },
                        { caseId: "permissive", policy: { minSamplesPerCohort: 2, minBaselineSuccessRate: 0.7, optionalEffectTolerance: 0.2, mandatoryEffectThreshold: 0.4, maxMandatorySuccessRate: 0.3 } },
                    ],
                }),
            });
            classificationResults.replaceChildren(
                resultCard("Sensitive services", String(report.sensitiveServices.length), report.sensitiveServices.join(", ") || "None"),
                resultCard("Robust services", String(report.robustServices.length), report.robustServices.join(", ") || "None"),
                resultCard("Policy cases", String(report.cases.length), "Strict and permissive threshold variants")
            );
            setLabStatus(experimentStatus, "Sensitivity analysis completed without changing stored evidence.");
        } catch (error) {
            setLabStatus(experimentStatus, error.message, true);
        } finally {
            runPolicySensitivity.disabled = false;
        }
    }

    async function evaluateRepeatability() {
        const input = document.querySelector('[data-role="repeatability-assessment-ids"]');
        const assessmentIds = input.value.split(",").map((value) => value.trim()).filter(Boolean);
        if (assessmentIds.length < 2) {
            setLabStatus(experimentStatus, "Enter at least two assessment IDs for repeatability analysis.", true);
            return;
        }
        runRepeatability.disabled = true;
        setLabStatus(experimentStatus, "Comparing independent assessment runs…");
        try {
            const report = await requestScenario("/api/workflows/dependency-experiments/repeatability", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ assessmentIds }),
            });
            const unstable = report.services.filter((service) => !service.stable);
            classificationResults.replaceChildren(
                resultCard("Mean agreement", formatRatioAsPercentage(report.meanAgreementRate), `${assessmentIds.length} immutable runs`),
                resultCard("Stable services", formatRatioAsPercentage(report.stableServiceRate), "Exact label agreement"),
                resultCard("Unstable services", String(unstable.length), unstable.map((service) => service.serviceName).join(", ") || "None")
            );
            setLabStatus(experimentStatus, "Repeatability comparison completed.");
        } catch (error) {
            setLabStatus(experimentStatus, error.message, true);
        } finally {
            runRepeatability.disabled = false;
        }
    }

    function scenarioChip(text) {
        const chip = document.createElement("span");
        chip.className = "contract-chip";
        chip.textContent = text;
        return chip;
    }

    function renderScenarioRegistry(scenarios) {
        if (!scenarioList) return;
        scenarioList.replaceChildren();
        if (!scenarios.length) {
            const empty = document.createElement("p");
            empty.className = "muted";
            empty.textContent = "No persisted workflow scenarios yet.";
            scenarioList.appendChild(empty);
            return;
        }
        scenarios.forEach((scenario) => {
            const record = document.createElement("article");
            record.className = "scenario-record";
            const heading = document.createElement("div");
            heading.className = "scenario-record__title";
            const name = document.createElement("strong");
            name.textContent = scenario.workflowId;
            heading.append(name, scenarioChip(`v${scenario.version}`));
            const description = document.createElement("p");
            description.textContent = scenario.description;
            const meta = document.createElement("div");
            meta.className = "scenario-record__meta";
            meta.append(
                scenarioChip(scenario.lifecycle),
                scenarioChip(scenario.source),
                scenarioChip(scenario.successCondition.protocol.toUpperCase()),
                scenarioChip(`criticality ${scenario.criticality.toFixed(2)}`)
            );
            record.append(heading, description, meta);
            scenarioList.appendChild(record);
        });
    }

    async function loadScenarioRegistry() {
        if (!scenarioList) return;
        try {
            renderScenarioRegistry(await requestScenario("/api/workflows/scenarios"));
        } catch (error) {
            scenarioList.textContent = `Scenario registry unavailable: ${error.message}`;
        }
    }

    function updateOutcomeGuidance() {
        if (!scenarioProtocol || !scenarioForm) return;
        const label = scenarioForm.querySelector('[data-role="scenario-outcomes-label"]');
        const help = scenarioForm.querySelector('[data-role="scenario-outcomes-help"]');
        const outcomes = scenarioForm.elements.outcomes;
        const guidance = {
            http: ["Accepted HTTP statuses", "Comma-separated numeric status codes.", "200"],
            grpc: ["Accepted gRPC statuses", "Canonical names such as OK or NOT_FOUND.", "OK"],
            span: ["Accepted span statuses", "Choose from UNSET, OK, and ERROR.", "OK"],
        }[scenarioProtocol.value];
        label.textContent = guidance[0];
        help.textContent = guidance[1];
        outcomes.value = guidance[2];
    }

    function scenarioPayload(form) {
        const values = new FormData(form);
        const protocol = values.get("protocol");
        const outcomes = String(values.get("outcomes"))
            .split(",")
            .map((value) => value.trim())
            .filter(Boolean);
        const successCondition = {
            protocol,
            terminalService: String(values.get("terminalService")).trim(),
            operationName: String(values.get("operationName")).trim(),
        };
        if (protocol === "http") {
            successCondition.httpStatusCodes = outcomes.map(Number);
        } else if (protocol === "grpc") {
            successCondition.grpcStatusCodes = outcomes.map((value) => value.toUpperCase());
        } else {
            successCondition.spanStatusCodes = outcomes.map((value) => value.toUpperCase());
        }
        const duration = String(values.get("maxDurationMs")).trim();
        if (duration) successCondition.maxDurationMs = Number(duration);
        return {
            workflowId: String(values.get("workflowId")).trim(),
            description: String(values.get("description")).trim(),
            criticality: Number(values.get("criticality")),
            entryService: String(values.get("entryService")).trim(),
            source: values.get("source"),
            lifecycle: values.get("lifecycle"),
            changeSummary: String(values.get("changeSummary")).trim(),
            successCondition,
        };
    }

    async function saveScenario(event) {
        event.preventDefault();
        if (!scenarioForm.reportValidity()) return;
        const actor = String(new FormData(scenarioForm).get("actor")).trim();
        const button = scenarioForm.querySelector('[data-role="save-scenario-button"]');
        button.disabled = true;
        setScenarioStatus("Saving immutable version 1…");
        try {
            const scenario = await requestScenario("/api/workflows/scenarios", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Actor": actor,
                },
                body: JSON.stringify(scenarioPayload(scenarioForm)),
            });
            setScenarioStatus(`Saved ${scenario.workflowId} version ${scenario.version}.`);
            await loadScenarioRegistry();
        } catch (error) {
            setScenarioStatus(error.message, true);
        } finally {
            button.disabled = false;
        }
    }

    function useDiscoveredCandidate() {
        if (!scenarioForm || !discoveredSelect || !discoveredSelect.value) return;
        const candidate = discoveredWorkflows.find(
            (item) => item.operationName === discoveredSelect.value
        );
        if (!candidate) return;
        const services = candidate.participatingServices || [];
        const entryService = entryServiceInput ? entryServiceInput.value.trim() : "frontend";
        scenarioForm.elements.workflowId.value = candidate.operationName
            .toLowerCase()
            .replace(/[^a-z0-9_-]+/g, "_")
            .replace(/^_+|_+$/g, "")
            .slice(0, 64);
        scenarioForm.elements.description.value = `Discovered operation ${candidate.operationName}`;
        scenarioForm.elements.entryService.value = entryService;
        scenarioForm.elements.source.value = "discovered";
        scenarioForm.elements.terminalService.value = services.at(-1) || entryService;
        scenarioForm.elements.operationName.value = candidate.operationName;
        scenarioForm.scrollIntoView({ behavior: "smooth", block: "start" });
        setScenarioStatus("Candidate copied. Review the business outcome before saving.");
    }

    function setExecutionStatus(message, isError = false) {
        if (!executionStatus) return;
        executionStatus.textContent = message;
        executionStatus.style.color = isError
            ? "var(--color-danger, #b91c1c)"
            : "var(--color-text-muted)";
    }

    function executionText(tag, className, value) {
        const element = document.createElement(tag);
        element.className = className;
        element.textContent = value;
        return element;
    }

    function renderExecutionLedger(outcomes) {
        if (!executionList) return;
        executionList.replaceChildren();
        if (!outcomes.length) {
            const empty = document.createElement("p");
            empty.className = "muted";
            empty.textContent = "No workflow execution outcomes recorded yet.";
            executionList.appendChild(empty);
            return;
        }
        outcomes.forEach((outcome) => {
            const record = document.createElement("article");
            record.className = `execution-record execution-record--${outcome.status}`;
            const identity = document.createElement("div");
            identity.className = "execution-record__identity";
            identity.append(
                executionText("strong", "", outcome.executionId),
                executionText(
                    "small",
                    "",
                    `${outcome.workflowId} · scenario v${outcome.scenarioVersion}`
                )
            );
            const status = executionText("span", "execution-record__status", outcome.status);
            const correlation = scenarioChip(outcome.correlationStatus);
            const passed = (outcome.assertions || []).filter((item) => item.passed).length;
            const assertionCount = (outcome.assertions || []).length;
            const evidence = executionText(
                "span",
                "execution-record__evidence",
                outcome.evidenceId
                    ? `${outcome.evidenceId} · ${passed}/${assertionCount} assertions passed`
                    : (outcome.reasons || ["No trace evidence"])[0]
            );
            record.append(identity, status, correlation, evidence);
            executionList.appendChild(record);
        });
    }

    async function loadExecutionLedger() {
        if (!executionList) return;
        try {
            renderExecutionLedger(await requestScenario("/api/workflows/executions"));
        } catch (error) {
            executionList.textContent = `Execution ledger unavailable: ${error.message}`;
        }
    }

    async function runBenchmarkExecutions() {
        if (!runBenchmarkBatchButton || !benchmarkSelect) return;
        const actor = executionActor ? executionActor.value.trim() : "";
        if (!actor) {
            setExecutionStatus("Run registered by is required.", true);
            return;
        }
        if (executionBatchId && !executionBatchId.reportValidity()) return;
        const batchId = executionBatchId ? executionBatchId.value.trim() : "";
        const params = batchId ? `?batch_id=${encodeURIComponent(batchId)}` : "";
        const endpoint = `/api/workflows/execution-batches/benchmarks/${encodeURIComponent(benchmarkSelect.value)}${params}`;
        runBenchmarkBatchButton.disabled = true;
        setExecutionStatus("Correlating benchmark traces…");
        try {
            const outcomes = await requestScenario(endpoint, {
                method: "POST",
                headers: {"X-Actor": actor},
            });
            const succeeded = outcomes.filter((item) => item.status === "succeeded").length;
            setExecutionStatus(
                `Recorded ${outcomes.length} executions; ${succeeded} succeeded.`
            );
            if (executionBatchId) executionBatchId.value = outcomes[0]?.batchId || batchId;
            await loadExecutionLedger();
        } catch (error) {
            setExecutionStatus(error.message, true);
        } finally {
            runBenchmarkBatchButton.disabled = false;
        }
    }

    function setAssessmentStatus(message, isError = false) {
        if (!assessmentStatus) return;
        assessmentStatus.textContent = message;
        assessmentStatus.style.color = isError
            ? "var(--color-danger, #b91c1c)"
            : "var(--color-text-muted)";
    }

    function assessmentClassName(classification) {
        return `assessment-service assessment-service--${classification}`;
    }

    function renderDependencyAssessments(assessments) {
        if (!assessmentList) return;
        assessmentList.replaceChildren();
        if (!assessments.length) {
            const empty = document.createElement("p");
            empty.className = "muted";
            empty.textContent = "No dependency evidence assessments recorded yet.";
            assessmentList.appendChild(empty);
            return;
        }
        assessments.forEach((assessment) => {
            const record = document.createElement("article");
            record.className = "dependency-assessment-record";
            const heading = document.createElement("div");
            heading.className = "dependency-assessment-record__header";
            const identity = document.createElement("div");
            identity.append(
                executionText("strong", "", assessment.assessmentId),
                executionText(
                    "small",
                    "",
                    `${assessment.workflowId} · scenario v${assessment.scenarioVersion} · ${assessment.environmentId}`
                )
            );
            const unresolved = assessment.services.filter(
                (item) => item.classification === "insufficient_evidence"
            );
            const approve = document.createElement("button");
            approve.type = "button";
            approve.className = "secondary-button";
            approve.textContent = unresolved.length ? "Evidence incomplete" : "Approve graph";
            approve.disabled = unresolved.length > 0;
            approve.addEventListener("click", () => approveAssessment(assessment.assessmentId));
            heading.append(identity, approve);

            const services = document.createElement("div");
            services.className = "dependency-assessment-record__services";
            assessment.services.forEach((item) => {
                const service = document.createElement("div");
                service.className = assessmentClassName(item.classification);
                const effect = item.failureEffect === null
                    ? "effect unavailable"
                    : `failure effect ${(item.failureEffect * 100).toFixed(1)}%`;
                service.append(
                    executionText("strong", "", item.serviceName),
                    executionText("span", "assessment-service__classification", item.classification.replaceAll("_", " ")),
                    executionText(
                        "small",
                        "",
                        `${effect} · evidence ${(item.evidenceStrength * 100).toFixed(0)}%`
                    ),
                    executionText("small", "", item.reasons[0] || "No explanation recorded")
                );
                services.appendChild(service);
            });
            record.append(heading, services);
            assessmentList.appendChild(record);
        });
    }

    async function loadDependencyAssessments() {
        if (!assessmentList) return;
        try {
            const workflowId = benchmarkSelect ? benchmarkSelect.value : "";
            const suffix = workflowId ? `?workflow_id=${encodeURIComponent(workflowId)}` : "";
            renderDependencyAssessments(
                await requestScenario(`/api/workflows/dependency-assessments${suffix}`)
            );
        } catch (error) {
            assessmentList.textContent = `Dependency assessments unavailable: ${error.message}`;
        }
    }

    function commaSeparatedIds(value) {
        return value.split(",").map((item) => item.trim()).filter(Boolean);
    }

    async function createAssessment(event) {
        event.preventDefault();
        if (!assessmentForm || !assessmentForm.reportValidity() || !benchmarkSelect) return;
        const values = new FormData(assessmentForm);
        const actor = String(values.get("actor")).trim();
        let interventions;
        try {
            interventions = JSON.parse(String(values.get("interventions")));
            if (!Array.isArray(interventions)) throw new Error("Expected a JSON array");
        } catch (error) {
            setAssessmentStatus(`Intervention JSON is invalid: ${error.message}`, true);
            return;
        }
        const button = assessmentForm.querySelector('[data-role="create-assessment-button"]');
        button.disabled = true;
        setAssessmentStatus("Evaluating baseline and intervention evidence…");
        try {
            const workflowId = benchmarkSelect.value;
            const graph = await requestScenario(
                `/api/workflows/benchmark/${encodeURIComponent(workflowId)}`
            );
            const payload = {
                assessmentId: String(values.get("assessmentId")).trim(),
                workflowId,
                scenarioVersion: 1,
                environmentId: String(values.get("environmentId")).trim(),
                source: "controlled_experiment",
                candidateGraph: graph,
                baselineBatchIds: commaSeparatedIds(String(values.get("baselineBatchIds"))),
                interventions,
            };
            const created = await requestScenario("/api/workflows/dependency-assessments", {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Actor": actor },
                body: JSON.stringify(payload),
            });
            setAssessmentStatus(`Created immutable assessment ${created.assessmentId}.`);
            await loadDependencyAssessments();
        } catch (error) {
            setAssessmentStatus(error.message, true);
        } finally {
            button.disabled = false;
        }
    }

    async function approveAssessment(assessmentId) {
        const actor = approvalActor ? approvalActor.value.trim() : "";
        const rationale = approvalRationale ? approvalRationale.value.trim() : "";
        if (!actor || !rationale) {
            setAssessmentStatus("Approval actor and rationale are required.", true);
            return;
        }
        try {
            await requestScenario(
                `/api/workflows/dependency-assessments/${encodeURIComponent(assessmentId)}/approve`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json", "X-Actor": actor },
                    body: JSON.stringify({ rationale }),
                }
            );
            setAssessmentStatus(`Approved validated graph from ${assessmentId}.`);
            await loadDependencyAssessments();
        } catch (error) {
            setAssessmentStatus(error.message, true);
        }
    }

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function renderGraph(container, workflow) {
        container.innerHTML = "";
        container.className = "workflow-dag-container";

        const edges = workflow.edges || [];
        const mandatorySet = new Set(workflow.mandatoryServices || []);
        const optionalSet = new Set(workflow.optionalServices || []);

        // Collect all unique nodes
        const allNodesSet = new Set([
            ...(workflow.observedServices || []),
            ...workflow.mandatoryServices,
            ...workflow.optionalServices,
            ...edges.map((e) => e.source),
            ...edges.map((e) => e.target),
        ]);
        const allNodes = Array.from(allNodesSet);

        if (!allNodes.length) {
            container.innerHTML = '<div class="muted">No services found in graph</div>';
            return;
        }

        // Build adjacency and in-degree maps
        const adj = new Map();
        const inDegree = new Map();
        allNodes.forEach((n) => {
            adj.set(n, []);
            inDegree.set(n, 0);
        });

        edges.forEach((e) => {
            if (adj.has(e.source) && adj.has(e.target)) {
                adj.get(e.source).push(e.target);
                inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
            }
        });

        // Determine topological ranks (levels) via BFS from roots (inDegree === 0)
        const nodeRank = new Map();
        const queue = [];

        allNodes.forEach((n) => {
            if ((inDegree.get(n) || 0) === 0) {
                nodeRank.set(n, 0);
                queue.push(n);
            }
        });

        // If cyclic or no 0-indegree, pick first node as root
        if (!queue.length && allNodes.length) {
            nodeRank.set(allNodes[0], 0);
            queue.push(allNodes[0]);
        }

        while (queue.length > 0) {
            const curr = queue.shift();
            const currRank = nodeRank.get(curr) || 0;
            const neighbors = adj.get(curr) || [];

            neighbors.forEach((nbr) => {
                const existing = nodeRank.get(nbr);
                if (existing === undefined) {
                    nodeRank.set(nbr, currRank + 1);
                    queue.push(nbr);
                }
            });
        }

        // Fallback for any unranked nodes
        allNodes.forEach((n) => {
            if (!nodeRank.has(n)) nodeRank.set(n, 1);
        });

        // Group nodes by level
        const levels = [];
        allNodes.forEach((n) => {
            const r = nodeRank.get(n) || 0;
            while (levels.length <= r) levels.push([]);
            levels[r].push(n);
        });

        // Layout dimensions
        const nodeW = 160;
        const nodeH = 46;
        const colGap = 70;
        const rowGap = 24;

        const maxLevelSize = Math.max(...levels.map((l) => l.length), 1);
        const svgW = Math.max(levels.length * (nodeW + colGap) + 40, 480);
        const svgH = Math.max(maxLevelSize * (nodeH + rowGap) + 40, 180);

        // Compute (x, y) for each node centered vertically within its level
        const positions = new Map();
        levels.forEach((levelNodes, colIdx) => {
            const colX = 20 + colIdx * (nodeW + colGap);
            const totalColH = levelNodes.length * nodeH + (levelNodes.length - 1) * rowGap;
            const startY = (svgH - totalColH) / 2;

            levelNodes.forEach((nodeName, rowIdx) => {
                const y = startY + rowIdx * (nodeH + rowGap);
                positions.set(nodeName, { x: colX, y: y });
            });
        });

        // Build SVG
        const ns = "http://www.w3.org/2000/svg";
        const svg = document.createElementNS(ns, "svg");
        svg.setAttribute("viewBox", `0 0 ${svgW} ${svgH}`);
        svg.classList.add("workflow-dag-svg");

        // Defs (Arrowheads)
        const defs = document.createElementNS(ns, "defs");
        defs.innerHTML = `
            <marker id="dag-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#94a3b8"></path>
            </marker>
            <marker id="dag-arrow-mandatory" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#6366f1"></path>
            </marker>
            <marker id="dag-arrow-active" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto">
                <path d="M 0 1.5 L 8 5 L 0 8.5 z" fill="#2563eb"></path>
            </marker>
        `;
        svg.appendChild(defs);

        // Edges group
        const edgesGroup = document.createElementNS(ns, "g");
        edgesGroup.classList.add("dag-edges-layer");

        edges.forEach((edge) => {
            const p1 = positions.get(edge.source);
            const p2 = positions.get(edge.target);
            if (!p1 || !p2) return;

            const startX = p1.x + nodeW;
            const startY = p1.y + nodeH / 2;
            const endX = p2.x;
            const endY = p2.y + nodeH / 2;

            const isMandatory = mandatorySet.has(edge.target);
            const isOptional = optionalSet.has(edge.target);
            const strokeColor = isMandatory ? "#6366f1" : isOptional ? "#f59e0b" : "#94a3b8";
            const markerId = isMandatory ? "dag-arrow-mandatory" : "dag-arrow";

            const path = document.createElementNS(ns, "path");
            const cX = (startX + endX) / 2;
            path.setAttribute("d", `M ${startX} ${startY} C ${cX} ${startY}, ${cX} ${endY}, ${endX} ${endY}`);
            path.setAttribute("stroke", strokeColor);
            path.setAttribute("stroke-width", "2");
            path.setAttribute("fill", "none");
            if (isOptional) path.setAttribute("stroke-dasharray", "5,4");
            path.setAttribute("class", "dag-edge-path");
            path.setAttribute("marker-end", `url(#${markerId})`);
            path.dataset.source = edge.source;
            path.dataset.target = edge.target;
            path.dataset.mandatory = isMandatory ? "true" : "false";

            edgesGroup.appendChild(path);
        });
        svg.appendChild(edgesGroup);

        // Nodes group
        const nodesGroup = document.createElementNS(ns, "g");
        nodesGroup.classList.add("dag-nodes-layer");

        allNodes.forEach((nodeName) => {
            const pos = positions.get(nodeName);
            if (!pos) return;

            const isEntry = (inDegree.get(nodeName) || 0) === 0;
            const isMandatory = mandatorySet.has(nodeName);
            const isOptional = optionalSet.has(nodeName);

            let fillColor = "#eef2ff";
            let strokeColor = "#6366f1";
            let badgeColor = "#4f46e5";
            let badgeText = "HIGH-FREQUENCY";
            let dashed = false;

            if (isEntry && !isMandatory) {
                fillColor = "#eff6ff";
                strokeColor = "#3b82f6";
                badgeColor = "#2563eb";
                badgeText = "INGRESS";
            } else if (isOptional) {
                fillColor = "#fffbeb";
                strokeColor = "#f59e0b";
                badgeColor = "#b45309";
                badgeText = "LOWER-FREQUENCY";
                dashed = true;
            } else if (isMandatory) {
                fillColor = "#eef2ff";
                strokeColor = "#6366f1";
                badgeColor = "#4f46e5";
                badgeText = "HIGH-FREQUENCY";
            } else {
                fillColor = "#f8fafc";
                strokeColor = "#94a3b8";
                badgeColor = "#64748b";
                badgeText = "SERVICE";
            }

            const g = document.createElementNS(ns, "g");
            g.setAttribute("class", "dag-node");
            g.setAttribute("transform", `translate(${pos.x}, ${pos.y})`);
            g.dataset.service = nodeName;

            const rect = document.createElementNS(ns, "rect");
            rect.setAttribute("width", String(nodeW));
            rect.setAttribute("height", String(nodeH));
            rect.setAttribute("rx", "8");
            rect.setAttribute("fill", fillColor);
            rect.setAttribute("stroke", strokeColor);
            rect.setAttribute("stroke-width", "1.5");
            rect.setAttribute("class", "dag-node-rect");
            if (dashed) rect.setAttribute("stroke-dasharray", "4,3");

            const nameText = document.createElementNS(ns, "text");
            nameText.setAttribute("x", "12");
            nameText.setAttribute("y", "20");
            nameText.setAttribute("fill", "#0f172a");
            nameText.setAttribute("font-size", "12");
            nameText.setAttribute("font-weight", "700");
            nameText.setAttribute("font-family", "system-ui, -apple-system, sans-serif");
            nameText.textContent = nodeName;

            const badge = document.createElementNS(ns, "text");
            badge.setAttribute("x", "12");
            badge.setAttribute("y", "36");
            badge.setAttribute("fill", badgeColor);
            badge.setAttribute("font-size", "10");
            badge.setAttribute("font-weight", "700");
            badge.setAttribute("font-family", "system-ui, -apple-system, sans-serif");
            badge.textContent = badgeText;
            g.append(rect, nameText, badge);

            // Hover interactions
            g.addEventListener("mouseenter", () => {
                svg.querySelectorAll(".dag-edge-path").forEach((p) => {
                    if (p.dataset.source === nodeName || p.dataset.target === nodeName) {
                        p.setAttribute("stroke", "#2563eb");
                        p.setAttribute("stroke-width", "3.5");
                        p.style.opacity = "1";
                        p.setAttribute("marker-end", "url(#dag-arrow-active)");
                    } else {
                        p.style.opacity = "0.2";
                    }
                });

                svg.querySelectorAll(".dag-node").forEach((n) => {
                    const s = n.dataset.service;
                    const isConnected =
                        s === nodeName ||
                        edges.some(
                            (e) =>
                                (e.source === nodeName && e.target === s) ||
                                (e.target === nodeName && e.source === s)
                        );
                    n.style.opacity = isConnected ? "1" : "0.3";
                });
            });

            g.addEventListener("mouseleave", () => {
                svg.querySelectorAll(".dag-edge-path").forEach((p) => {
                    const isMand = p.dataset.mandatory === "true";
                    p.setAttribute("stroke", isMand ? "#6366f1" : "#94a3b8");
                    p.setAttribute("stroke-width", "2");
                    p.style.opacity = "1";
                    p.setAttribute("marker-end", `url(#${isMand ? "dag-arrow-mandatory" : "dag-arrow"})`);
                });
                svg.querySelectorAll(".dag-node").forEach((n) => {
                    n.style.opacity = "1";
                });
            });

            nodesGroup.appendChild(g);
        });


        svg.appendChild(nodesGroup);
        container.appendChild(svg);
    }


    function renderServiceChips(container, services) {
        container.innerHTML = "";
        if (!services.length) {
            const empty = document.createElement("span");
            empty.className = "muted";
            empty.textContent = "None";
            container.appendChild(empty);
            return;
        }

        services.forEach((service) => {
            const chip = document.createElement("span");
            chip.className = "contract-chip";
            chip.textContent = service;
            container.appendChild(chip);
        });
    }

    function renderEdgeTable(tbody, workflow) {
        tbody.innerHTML = "";
        workflow.edges.forEach((edge) => {
            const row = document.createElement("tr");

            const isMandatory = workflow.mandatoryServices.includes(edge.target);
            const isOptional = workflow.optionalServices.includes(edge.target);

            const badgeClass = isMandatory
                ? "status-badge status-badge--success"
                : isOptional
                ? "status-badge status-badge--warning"
                : "status-badge status-badge--muted status-badge--no-dot";

            const typeLabel = isMandatory
                ? "HIGH-FREQUENCY CANDIDATE"
                : isOptional
                ? "LOWER-FREQUENCY CANDIDATE"
                : "OBSERVED (Not classified)";

            const sourceCell = document.createElement("td");
            const sourceCode = document.createElement("code");
            sourceCode.textContent = edge.source;
            sourceCell.appendChild(sourceCode);
            const targetCell = document.createElement("td");
            const targetCode = document.createElement("code");
            targetCode.textContent = edge.target;
            targetCell.appendChild(targetCode);
            const typeCell = document.createElement("td");
            const typeBadge = document.createElement("span");
            typeBadge.className = badgeClass;
            typeBadge.textContent = typeLabel;
            typeCell.appendChild(typeBadge);
            row.append(sourceCell, targetCell, typeCell);

            tbody.appendChild(row);
        });
    }


    function renderWorkflowData(workflow, isLive = false) {
        liveMode = isLive;
        renderVersion += 1;
        lastSensitivitySteps = [];
        region.innerHTML = "";
        region.appendChild(template.content.cloneNode(true));

        setMetric(region, "workflow-id", workflow.workflowId);
        setMetric(
            region,
            "workflow-criticality",
            formatRatioAsPercentage(workflow.criticality)
        );
        setMetric(
            region,
            "workflow-mandatory-count",
            String(workflow.mandatoryServices.length)
        );
        setMetric(
            region,
            "workflow-optional-count",
            String(workflow.optionalServices.length)
        );
        setMetric(region, "workflow-edge-count", String(workflow.edges.length));

        renderGraph(region.querySelector('[data-role="workflow-graph"]'), workflow);
        renderServiceChips(
            region.querySelector('[data-role="workflow-mandatory-list"]'),
            workflow.mandatoryServices
        );
        renderServiceChips(
            region.querySelector('[data-role="workflow-optional-list"]'),
            workflow.optionalServices
        );
        renderEdgeTable(
            region.querySelector('[data-role="workflow-edge-table-body"]'),
            workflow
        );

        initJsonPanel(
            region.querySelector("#workflow-raw-json"),
            workflow
        );

        if (dataSourceBadge) {
            dataSourceBadge.textContent = isLive ? "Jaeger • origin unverified" : "SYNTHETIC FIXTURE";
            dataSourceBadge.className = isLive
                ? "status-badge status-badge--success"
                : "status-badge status-badge--muted status-badge--no-dot";
        }

        updateLastRefreshed(lastRefreshed);
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = isLive ? 'Jaeger • origin unverified' : 'Synthetic fixture';
        initEvaluationListeners();
        const note = region.querySelector('[data-role="evidence-note"]');
        note.textContent = isLive
            ? `${workflow.traceCount} matching traces from ${workflow.fetchedTraceCount} fetched (limit ${workflow.sampleLimit}). Jaeger may contain synthetic demo traces; evidence origin and execution success have not been verified. These are observed calls, with no validated dependency classification or accuracy score. Missing services may be uninstrumented.`
            : 'Synthetic engineering fixture, not empirical benchmark evidence. Prediction uses only the scenario and trace evidence; the separate reference is loaded afterward for comparison. Frequency-based candidates do not prove logical necessity, and combined-service scores do not validate mandatory classification.';
        if (isLive) {
            setMetric(region, "workflow-criticality", "Not set");
            setMetric(region, "workflow-mandatory-count", "Not validated");
            setMetric(region, "workflow-optional-count", "Not validated");
            region.querySelectorAll('[data-role="benchmark-only"]').forEach(el => el.style.display = 'none');
            region.querySelectorAll('.graph-legend, .section-header__note').forEach(el => el.style.display = 'none');
            const evidence = region.querySelector('[data-role="live-service-evidence"]');
            evidence.hidden = false;
            workflow.serviceEvidence.forEach(item => {
                const row = document.createElement('p');
                row.textContent = `${item.service}: observed in ${item.traceCount}/${workflow.traceCount} traces (${Math.round(item.frequency * 100)}%)`;
                evidence.appendChild(row);
            });
        } else {
            handleRunEvaluation();
        }
    }


    async function checkJaegerHealth() {
        if (!jaegerStatusBadge || !jaegerUrlInput) return;
        try {
            const url = `/api/workflows/jaeger/status?jaeger_url=${encodeURIComponent(jaegerUrlInput.value.trim())}`;
            const res = await fetchJson(url);
            if (res.healthy) {
                jaegerStatusBadge.textContent = "Jaeger Online";
                jaegerStatusBadge.className = "status-badge status-badge--success";
            } else {
                jaegerStatusBadge.textContent = "Jaeger Offline";
                jaegerStatusBadge.className = "status-badge status-badge--danger";
            }
        } catch (err) {
            jaegerStatusBadge.textContent = "Jaeger Offline";
            jaegerStatusBadge.className = "status-badge status-badge--danger";
        }
    }

    async function handleDiscoverWorkflows() {
        if (!discoverButton) return;
        discoverButton.disabled = true;
        discoverButton.textContent = "Discovering…";

        const jaegerUrl = jaegerUrlInput ? jaegerUrlInput.value.trim() : "http://localhost:16686";
        const serviceName = entryServiceInput ? entryServiceInput.value.trim() : "frontend";
        const lookback = lookbackSelect ? lookbackSelect.value : "1h";

        try {
            const url = `/api/workflows/discover?limit=1000&service_name=${encodeURIComponent(serviceName)}&jaeger_url=${encodeURIComponent(jaegerUrl)}&lookback=${encodeURIComponent(lookback)}`;
            discoveredWorkflows = await fetchJson(url);

            if (discoveredSelect && discoveredContainer) {
                discoveredSelect.innerHTML = "";
                if (discoveredWorkflows.length > 0) {
                    discoveredWorkflows.forEach((w) => {
                        const opt = document.createElement("option");
                        opt.value = w.operationName;
                        opt.textContent = `${w.operationName} (${w.traceCount} traces, ${w.participatingServices.length} services)`;
                        discoveredSelect.appendChild(opt);
                    });
                    discoveredContainer.style.display = "block";
                    if (extractLiveButton) extractLiveButton.disabled = false;
                } else {
                    const opt = document.createElement("option");
                    opt.value = "";
                    opt.textContent = "No traces found in Jaeger yet for this service";
                    discoveredSelect.appendChild(opt);
                    discoveredContainer.style.display = "block";
                    if (extractLiveButton) extractLiveButton.disabled = true;
                }
            }
        } catch (error) {
            alert(`Could not connect to Jaeger: ${error.message}`);
        } finally {
            discoverButton.disabled = false;
            discoverButton.textContent = "Discover Candidate Operations";
        }
    }

    async function handleExtractLive() {
        if (!extractLiveButton) return;
        const operation = discoveredSelect ? discoveredSelect.value : "";
        if (!operation) {
            alert("Please select a candidate operation from the dropdown.");
            return;
        }

        extractLiveButton.disabled = true;
        extractLiveButton.textContent = "Extracting…";

        const jaegerUrl = jaegerUrlInput ? jaegerUrlInput.value.trim() : "http://localhost:16686";
        const serviceName = entryServiceInput ? entryServiceInput.value.trim() : "frontend";
        const lookback = lookbackSelect ? lookbackSelect.value : "1h";

        try {
            const url = `/api/workflows/live/evidence?service_name=${encodeURIComponent(serviceName)}&operation=${encodeURIComponent(operation)}&jaeger_url=${encodeURIComponent(jaegerUrl)}&lookback=${encodeURIComponent(lookback)}`;
            const workflow = await fetchJson(url);
            renderWorkflowData(workflow, true);
        } catch (error) {
            alert(`Live extraction failed: ${error.message}`);
        } finally {
            extractLiveButton.disabled = false;
            extractLiveButton.textContent = "Extract Live Dependency Graph";
        }
    }


    const benchmarkSelect = document.querySelector('[data-role="benchmark-select"]');

    async function loadBenchmarkWorkflow() {
        if (reloadSampleButton) reloadSampleButton.disabled = true;
        const workflowId = benchmarkSelect ? benchmarkSelect.value : "checkout";
        const endpoint = `/api/workflows/benchmark/${encodeURIComponent(workflowId)}`;

        try {
            const workflow = await fetchJson(endpoint);
            renderWorkflowData(workflow, false);
        } catch (error) {
            setError(region, {
                endpoint: endpoint,
                message: error.message,
                onRetry: loadBenchmarkWorkflow,
            });
            console.error("Failed to load benchmark workflow:", error);
        } finally {
            if (reloadSampleButton) reloadSampleButton.disabled = false;
        }
    }

    const seedTracesButton = document.querySelector('[data-role="seed-traces-button"]');

    async function handleSeedTraces() {
        if (!seedTracesButton) return;
        seedTracesButton.disabled = true;
        seedTracesButton.textContent = "Seeding…";

        try {
            const response = await fetch("/api/workflows/jaeger/seed", { method: "POST" });
            const data = await response.json();
            if (response.ok) {
                // Automatically run discovery right after seeding
                await handleDiscoverWorkflows();
            } else {
                alert(`Failed to seed traces: ${data.detail || "Unknown error"}`);
            }
        } catch (err) {
            alert(`Could not seed traces: ${err.message}`);
        } finally {
            seedTracesButton.disabled = false;
            seedTracesButton.textContent = "Seed Synthetic Demo Traces";
        }
    }

    // ---------------------------------------------------------------------------
    // Research Evaluation & Sensitivity Analysis Handlers
    // ---------------------------------------------------------------------------

    let lastSensitivitySteps = [];

    async function handleRunEvaluation() {
        if (liveMode) return;
        const version = renderVersion;
        const slider = document.querySelector('[data-role="eval-threshold-slider"]');
        const threshold = slider ? parseFloat(slider.value) : 1.0;
        const workflowId = benchmarkSelect ? benchmarkSelect.value : "checkout";

        const btn = document.querySelector('[data-role="run-eval-btn"]');
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Evaluating…";
        }

        try {
            const url = `/api/workflows/evaluation/accuracy?workflow_id=${encodeURIComponent(workflowId)}&mandatory_threshold=${threshold}`;
            const data = await fetchJson(url);
            if (liveMode || version !== renderVersion) return;

            const precisionEl = document.querySelector('[data-role="eval-precision"]');
            const recallEl = document.querySelector('[data-role="eval-recall"]');
            const f1El = document.querySelector('[data-role="eval-f1"]');
            const edgeAccEl = document.querySelector('[data-role="eval-edge-acc"]');

            if (precisionEl) precisionEl.textContent = formatRatioAsPercentage(data.serviceMetrics.precision);
            if (recallEl) recallEl.textContent = formatRatioAsPercentage(data.serviceMetrics.recall);
            if (f1El) f1El.textContent = data.serviceMetrics.f1.toFixed(2);
            if (edgeAccEl) edgeAccEl.textContent = formatRatioAsPercentage(data.edgeMetrics.accuracy);
        } catch (err) {
            alert(`Evaluation failed: ${err.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = "Compare Fixture Sets";
            }
        }
    }

    async function handleRunSensitivity() {
        if (liveMode) return;
        const version = renderVersion;
        const workflowId = benchmarkSelect ? benchmarkSelect.value : "checkout";
        const btn = document.querySelector('[data-role="run-sensitivity-btn"]');
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Running…";
        }

        try {
            const url = `/api/workflows/evaluation/sensitivity?workflow_id=${encodeURIComponent(workflowId)}`;
            const steps = await fetchJson(url);
            if (liveMode || version !== renderVersion) return;
            lastSensitivitySteps = steps;

            const container = document.querySelector('[data-role="sensitivity-table-container"]');
            const tbody = document.querySelector('[data-role="sensitivity-table-body"]');

            if (container && tbody) {
                tbody.innerHTML = "";
                steps.forEach((step) => {
                    const tr = document.createElement("tr");
                    tr.innerHTML = `
                        <td><strong>${step.threshold.toFixed(2)}</strong></td>
                        <td>${formatRatioAsPercentage(step.precision)}</td>
                        <td>${formatRatioAsPercentage(step.recall)}</td>
                        <td><strong>${step.f1.toFixed(2)}</strong></td>
                        <td>${formatRatioAsPercentage(step.edgeAccuracy)}</td>
                        <td><span class="contract-chip">${step.mandatoryServicesCount}</span></td>
                        <td><span class="contract-chip">${step.optionalServicesCount}</span></td>
                    `;
                    tbody.appendChild(tr);
                });
                container.style.display = "block";
            }
        } catch (err) {
            alert(`Sensitivity analysis failed: ${err.message}`);
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = "Compare Thresholds";
            }
        }
    }

    function handleCopyMarkdownTable() {
        if (liveMode || !lastSensitivitySteps.length) {
            alert("Run a fixture threshold comparison before copying its results.");
            return;
        }
        const workflowId = benchmarkSelect ? benchmarkSelect.value : "checkout";
        let markdown = `### Synthetic fixture threshold comparison: \`${workflowId}\`\n\n`;
        markdown += 'Engineering fixture only. Prediction is reference-independent, but frequency-based candidates do not prove logical necessity. Combined-service scores do not measure mandatory classification or empirical benchmark accuracy.\n\n';
        markdown += `| Candidate Threshold (θ) | Precision | Recall | F1-Score | Edge Accuracy | High-frequency Count | Lower-frequency Count |\n`;
        markdown += `| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n`;

        if (lastSensitivitySteps.length > 0) {
            lastSensitivitySteps.forEach((s) => {
                markdown += `| **${s.threshold.toFixed(2)}** | ${(s.precision * 100).toFixed(1)}% | ${(s.recall * 100).toFixed(1)}% | **${s.f1.toFixed(2)}** | ${(s.edgeAccuracy * 100).toFixed(1)}% | ${s.mandatoryServicesCount} | ${s.optionalServicesCount} |\n`;
            });
        } else {
            return;
        }

        navigator.clipboard.writeText(markdown).then(() => {
            alert("Fixture table copied with its research limitations. These are not empirical results.");
        }).catch(() => {
            prompt("Copy this Markdown table:", markdown);
        });
    }

    function initEvaluationListeners() {
        const slider = document.querySelector('[data-role="eval-threshold-slider"]');
        const display = document.querySelector('[data-role="eval-threshold-display"]');
        const evalBtn = document.querySelector('[data-role="run-eval-btn"]');
        const sensitivityBtn = document.querySelector('[data-role="run-sensitivity-btn"]');
        const copyBtn = document.querySelector('[data-role="copy-markdown-btn"]');

        if (slider && display) {
            slider.addEventListener("input", () => {
                display.textContent = `${parseFloat(slider.value).toFixed(2)} (${Math.round(slider.value * 100)}%)`;
            });
        }

        if (evalBtn) evalBtn.addEventListener("click", handleRunEvaluation);
        if (sensitivityBtn) sensitivityBtn.addEventListener("click", handleRunSensitivity);
        if (copyBtn) copyBtn.addEventListener("click", handleCopyMarkdownTable);
    }

    if (reloadSampleButton) {
        reloadSampleButton.addEventListener("click", loadBenchmarkWorkflow);
    }
    if (benchmarkSelect) {
        benchmarkSelect.addEventListener("change", () => {
            loadBenchmarkWorkflow();
            setTimeout(handleRunEvaluation, 100);
        });
    }
    if (seedTracesButton) {
        seedTracesButton.addEventListener("click", handleSeedTraces);
    }
    if (discoverButton) {
        discoverButton.addEventListener("click", handleDiscoverWorkflows);
    }

    if (extractLiveButton) {
        extractLiveButton.addEventListener("click", handleExtractLive);
    }
    if (useCandidateButton) {
        useCandidateButton.addEventListener("click", useDiscoveredCandidate);
    }
    if (scenarioProtocol) {
        scenarioProtocol.addEventListener("change", updateOutcomeGuidance);
    }
    if (scenarioForm) {
        scenarioForm.addEventListener("submit", saveScenario);
    }
    if (refreshScenariosButton) {
        refreshScenariosButton.addEventListener("click", loadScenarioRegistry);
    }
    if (runBenchmarkBatchButton) {
        runBenchmarkBatchButton.addEventListener("click", runBenchmarkExecutions);
    }
    if (refreshExecutionsButton) {
        refreshExecutionsButton.addEventListener("click", loadExecutionLedger);
    }
    if (assessmentForm) {
        assessmentForm.addEventListener("submit", createAssessment);
    }
    if (refreshAssessmentsButton) {
        refreshAssessmentsButton.addEventListener("click", loadDependencyAssessments);
    }
    if (jaegerUrlInput) {
        jaegerUrlInput.addEventListener("change", checkJaegerHealth);
    }
    if (referenceForm) referenceForm.addEventListener("submit", saveDependencyReference);
    if (refreshClassificationLab) refreshClassificationLab.addEventListener("click", loadClassificationLab);
    if (runClassificationEvaluation) runClassificationEvaluation.addEventListener("click", evaluateClassification);
    if (runPolicySensitivity) runPolicySensitivity.addEventListener("click", evaluatePolicySensitivity);
    if (runRepeatability) runRepeatability.addEventListener("click", evaluateRepeatability);
    if (guidedForm) guidedForm.addEventListener("submit", discoverGuidedWorkflows);
    if (guidedInspectButton) guidedInspectButton.addEventListener("click", inspectGuidedWorkflow);
    if (researchAudit) {
        researchAudit.addEventListener("toggle", () => {
            if (!researchAudit.open || researchAuditInitialized) return;
            researchAuditInitialized = true;
            loadBenchmarkWorkflow();
            checkJaegerHealth();
            loadScenarioRegistry();
            loadExecutionLedger();
            loadDependencyAssessments();
            loadClassificationLab();
            initEvaluationListeners();
            setTimeout(handleRunEvaluation, 300);
        });
    }
    stageButtons.forEach((button) => {
        button.addEventListener("click", () => {
            activateWorkflowStage(button.dataset.workflowStageButton);
        });
    });

    document.addEventListener("DOMContentLoaded", () => {
        activateWorkflowStage("define");
        loadLatestExperiment();
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = "No active campaign";
    });
})();
