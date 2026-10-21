/*
 * Workflow Dependency Modeling page script.
 * Owned by: Member 1 (A.D. Perera).
 *
 * Live Jaeger trace ingestion & automated discovery. No sample or synthetic workflow data is loaded.
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
    const extractLiveButton = document.querySelector('[data-role="extract-live-button"]');
    const discoveredContainer = document.querySelector('[data-role="discovered-workflows-container"]');
    const discoveredSelect = document.querySelector('[data-role="discovered-workflow-select"]');
    const liveConfigStatus = document.querySelector('[data-role="live-config-status"]');
    const useCandidateButton = document.querySelector('[data-role="use-candidate-button"]');
    const scenarioForm = document.querySelector('[data-role="scenario-form"]');
    const scenarioProtocol = document.querySelector('[data-role="scenario-protocol"]');
    const scenarioStatus = document.querySelector('[data-role="scenario-form-status"]');
    const scenarioList = document.querySelector('[data-role="scenario-registry-list"]');
    const refreshScenariosButton = document.querySelector('[data-role="refresh-scenarios-button"]');
    const executionStatus = document.querySelector('[data-role="execution-runner-status"]');
    const executionList = document.querySelector('[data-role="execution-ledger-list"]');
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
    const guidedObserved = document.querySelector('[data-role="guided-observed"]');
    const guidedObservedState = document.querySelector('[data-role="guided-observed-state"]');
    const guidedObservedDetail = document.querySelector('[data-role="guided-observed-detail"]');
    const guidedObservedChecked = document.querySelector('[data-role="guided-observed-checked"]');
    const guidedCandidates = document.querySelector('[data-role="guided-candidates"]');
    const findButton = document.querySelector('[data-role="find-workflows-button"]');
    const findStatus = document.querySelector('[data-role="find-workflows-status"]');
    const startPrompt = document.querySelector('[data-role="trace-start-prompt"]');
    const startConfirm = document.querySelector('[data-role="trace-start-confirm"]');
    const startCancel = document.querySelector('[data-role="trace-start-cancel"]');
    const stopButton = document.querySelector('[data-role="trace-stop-button"]');
    const guidedAddress = document.getElementById("guided-jaeger-url");
    const guidedLookback = document.getElementById("guided-lookback");
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
            let detail = data.detail;
            if (Array.isArray(detail)) {
                detail = detail
                    .map((item) => {
                        const location = Array.isArray(item.loc)
                            ? item.loc.filter((part) => part !== "body").join(" → ")
                            : "request";
                        return `${location || "request"}: ${item.msg || "invalid value"}`;
                    })
                    .join("; ");
            } else if (detail && typeof detail === "object") {
                detail = JSON.stringify(detail);
            }
            const error = new Error(detail || `Request failed with HTTP ${response.status}`);
            error.status = response.status;
            throw error;
        }
        return data;
    }

    // One candidate is one root operation under one entry service; the same operation name can occur under two.
    function workflowKey(workflow) {
        return `${workflow.rootService || ""} ${workflow.operationName}`;
    }

    function selectedGuidedWorkflow() {
        return guidedOperation
            ? guidedWorkflows.find((workflow) => workflowKey(workflow) === guidedOperation.value)
            : undefined;
    }

    // Only the Online Boutique benchmark has a controlled checkout experiment; it is offered for the
    // operations that name checkout and is never a default for any other workflow.
    function supportsGuidedExperiment(workflow) {
        return Boolean(
            workflow &&
            String(workflow.operationName || "").toLowerCase().includes("checkout")
        );
    }

    function updateGuidedSelectionAction() {
        if (!guidedOperation || !guidedInspectButton) return;
        const selected = selectedGuidedWorkflow();
        guidedInspectButton.disabled = !selected;
        guidedInspectButton.textContent = !selected
            ? "No workflow available"
            : supportsGuidedExperiment(selected)
                ? "Open checkout experiment"
                : "View observed services";
    }

    function guidedConnectionPayload() {
        const values = new FormData(guidedForm);
        return {
            environmentId: String(values.get("environmentId")).trim(),
            evidenceTier: String(values.get("evidenceTier")),
            jaegerUrl: String(values.get("jaegerUrl")).trim(),
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
            return "The workflow trace service could not be reached. The page keeps checking and loads workflows by itself once it answers.";
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
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = "Campaign active";

        const heading = document.createElement("div");
        heading.className = "guided-result__heading";
        const identity = document.createElement("div");
        identity.append(
            guidedText("span", "guided-result__label", "Checkout dependency experiment"),
            guidedText("h4", "", "Find which services checkout truly requires"),
            guidedText(
                "p",
                "",
                "First prove normal checkout works consistently. Later, test one service at a time and compare the outcome."
            )
        );
        const statusLabels = {
            baseline_required: "Step 2: baseline needed",
            intervention_required: "Step 3: service tests needed",
            assessment_ready: "Step 4: ready to classify",
            approved: "Step 5: graph approved",
        };
        heading.append(
            identity,
            guidedText(
                "span",
                "guided-maturity",
                statusLabels[campaign.status] || campaign.status.replaceAll("_", " ")
            )
        );

        const metrics = document.createElement("div");
        metrics.className = "guided-result__metrics";
        metrics.append(
            resultCard("Normal checkouts needed", `${campaign.baseline.requiredTrials} runs`, "Before testing any failure"),
            resultCard("Services to test", String(campaign.candidateServices.length), "Tested one at a time later"),
            resultCard("Initial records", `${campaign.observedTraceCount} checkouts`, "Show participation only")
        );

        const contract = document.createElement("section");
        contract.className = "guided-contract guided-contract--confirmed";
        contract.append(
            guidedText("span", "guided-result__label", "How success is measured"),
            guidedText("h5", "", "A customer receives a confirmed order"),
            guidedText(
                "p",
                "",
                "The checkout page must complete successfully and display the order confirmation."
            )
        );

        const baseline = document.createElement("section");
        baseline.className = "baseline-console";
        baseline.dataset.role = "baseline-console";
        const baselineHeading = document.createElement("div");
        baselineHeading.className = "baseline-console__heading";
        baselineHeading.append(
            guidedText("div", "", "STEP 2 OF 5  ·  CONFIRM NORMAL BEHAVIOR"),
            guidedText("strong", "", `Run ${campaign.baseline.requiredTrials} normal checkouts before testing a service failure`)
        );
        const baselineControls = document.createElement("div");
        baselineControls.className = "baseline-console__controls";
        const frontendLabel = guidedText("label", "", "Demo store address");
        const frontendUrl = document.createElement("input");
        frontendUrl.type = "url";
        frontendUrl.value = "http://localhost:8080";
        frontendUrl.required = true;
        frontendLabel.appendChild(frontendUrl);
        const runButton = guidedText("button", "experiment-button", "Run one normal checkout");
        runButton.type = "button";
        const runStatus = guidedText("span", "baseline-console__status", "No cluster change is applied.");
        baselineControls.append(frontendLabel, runButton, runStatus);
        const readiness = document.createElement("div");
        readiness.className = "baseline-readiness";
        baseline.append(baselineHeading, baselineControls, readiness);
        runButton.addEventListener("click", async () => {
            if (!frontendUrl.reportValidity()) return;
            runButton.disabled = true;
        runStatus.textContent = "Running one checkout and matching it to its activity record…";
            runStatus.classList.remove("is-error");
            try {
                const outcome = await requestScenario(
                    `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/baseline-runs`,
                    {
                        method: "POST",
                        headers: {
                            "Content-Type": "application/json",
                            "X-Actor": "guided-local-runner",
                        },
                        body: JSON.stringify({frontendUrl: frontendUrl.value.trim()}),
                    }
                );
                runStatus.textContent = outcome.correlationStatus === "matched"
                    ? `Trial recorded: ${outcome.status}.`
                    : `Trial excluded: trace correlation is ${outcome.correlationStatus}.`;
                await loadBaselineReadiness(
                    campaign,
                    readiness,
                    runButton,
                    runStatus,
                    interventionConsole,
                    frontendUrl
                );
            } catch (error) {
                runStatus.textContent = guidedConnectionError(error);
                runStatus.classList.add("is-error");
                runButton.disabled = false;
            }
        });

        const runbook = document.createElement("section");
        runbook.className = "campaign-runbook";
        runbook.appendChild(guidedText("h5", "", "Services that will be tested after the baseline"));
        const list = document.createElement("div");
        list.className = "campaign-runbook__list";
        campaign.interventions.forEach((intervention, index) => {
            const item = document.createElement("article");
            item.className = "campaign-runbook__item";
            item.append(
                guidedText("span", "campaign-runbook__number", String(index + 1).padStart(2, "0")),
                guidedText("strong", "", intervention.targetService),
                guidedText("small", "", "Temporarily unavailable · run checkout · restore")
            );
            list.appendChild(item);
        });
        runbook.appendChild(list);

        const interventionConsole = document.createElement("section");
        interventionConsole.className = "intervention-console";
        interventionConsole.hidden = true;

        const next = document.createElement("div");
        next.className = "guided-next";
        next.appendChild(guidedText("h5", "", "What remains before the dependency result is available"));
        const blockers = document.createElement("ol");
        campaign.blockers.forEach((blocker) => blockers.appendChild(guidedText("li", "", blocker)));
        next.appendChild(blockers);

        guidedResult.append(heading, metrics, contract, baseline, runbook, interventionConsole, next);
        loadBaselineReadiness(
            campaign,
            readiness,
            runButton,
            runStatus,
            interventionConsole,
            frontendUrl
        );
        if (dataSourceBadge) {
            dataSourceBadge.textContent = "EXPERIMENT · BASELINE REQUIRED";
            dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
        }
    }

    async function loadBaselineReadiness(
        campaign,
        target,
        runButton,
        runStatus,
        interventionConsole,
        frontendUrl
    ) {
        try {
            const readiness = await requestScenario(
                `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/baseline-readiness`
            );
            target.replaceChildren();
            const counts = document.createElement("div");
            counts.className = "baseline-readiness__counts";
            counts.append(
                resultCard("Eligible", `${readiness.eligibleTrials}/${readiness.requiredTrials}`, "Exactly correlated"),
                resultCard("Succeeded", String(readiness.successfulTrials), "Met the success contract"),
                resultCard("Failed", String(readiness.failedTrials), "Business outcome failed"),
                resultCard("Excluded", String(readiness.excludedTrials), "Missing or ambiguous evidence")
            );
            const gate = document.createElement("div");
            gate.className = readiness.ready
                ? "baseline-readiness__gate is-ready"
                : "baseline-readiness__gate";
            gate.appendChild(guidedText(
                "strong",
                "",
                readiness.ready ? "Baseline gate passed" : "Baseline gate not yet satisfied"
            ));
            if (readiness.blockers.length) {
                const list = document.createElement("ul");
                readiness.blockers.forEach((blocker) => list.appendChild(guidedText("li", "", blocker)));
                gate.appendChild(list);
            } else {
                gate.appendChild(guidedText("span", "", "Intervention cohorts may now be collected one service at a time."));
            }
            target.append(counts, gate);
            runButton.disabled = readiness.ready;
            if (readiness.ready) {
                runButton.textContent = "Baseline complete";
                runStatus.textContent = "The clean checkout baseline is stable and fully correlated.";
                updateExperimentPath(2);
                if (dataSourceBadge) dataSourceBadge.textContent = "EXPERIMENT · BASELINE READY";
                interventionConsole.hidden = false;
                await loadInterventionConsole(campaign, interventionConsole, frontendUrl);
            }
        } catch (error) {
            target.replaceChildren(guidedText("p", "baseline-console__status is-error", error.message));
            runButton.disabled = false;
        }
    }

    async function postCampaignAction(url, payload) {
        return requestScenario(url, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-Actor": "guided-local-researcher",
            },
            body: JSON.stringify(payload),
        });
    }

    function interventionActionButton(label, action) {
        const button = guidedText("button", "experiment-button", label);
        button.type = "button";
        button.addEventListener("click", action);
        return button;
    }

    async function loadInterventionConsole(campaign, target, frontendUrl) {
        target.replaceChildren();
        const heading = document.createElement("div");
        heading.className = "baseline-console__heading";
        heading.append(
            guidedText("div", "", "03  ·  CONTROLLED INTERVENTIONS"),
            guidedText("strong", "", "Test one downstream service at a time")
        );
        target.append(
            heading,
            guidedText(
                "p",
                "intervention-console__notice",
                "This component never changes the cluster. Use your approved Docker or Kubernetes procedure, record what you changed, then confirm the state here."
            )
        );
        try {
            const status = await requestScenario(
                `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/intervention-status`
            );
            const list = document.createElement("div");
            list.className = "intervention-console__list";
            status.services.forEach((service) => {
                list.appendChild(renderInterventionService(campaign, service, target, frontendUrl));
            });
            target.appendChild(list);
            if (status.services.every((service) => service.evidenceReady)) {
                updateExperimentPath(3);
                const decision = document.createElement("section");
                decision.className = "assessment-console";
                target.appendChild(decision);
                await loadCampaignDecision(campaign, decision);
            }
        } catch (error) {
            target.appendChild(guidedText("p", "baseline-console__status is-error", error.message));
        }
    }

    function renderInterventionService(campaign, service, target, frontendUrl) {
        const card = document.createElement("article");
        card.className = `intervention-service intervention-service--${service.state}`;
        const title = document.createElement("div");
        title.className = "intervention-service__title";
        title.append(
            guidedText("strong", "", service.targetService),
            guidedText("span", "guided-maturity", service.evidenceReady ? "evidence ready" : service.state)
        );
        const progress = guidedText(
            "p",
            "",
            `${service.eligibleTrials}/${service.requiredTrials} eligible checkout trials · ${service.excludedTrials} excluded`
        );
        const controls = document.createElement("div");
        controls.className = "intervention-service__controls";
        const evidence = document.createElement("input");
        evidence.type = "text";
        evidence.placeholder = service.state === "active"
            ? "Restoration evidence (ticket, command result, observation)"
            : "Activation evidence (ticket, command result, observation)";
        evidence.setAttribute("aria-label", `${service.targetService} operator evidence`);
        const message = guidedText("span", "baseline-console__status", "");

        const refresh = () => loadInterventionConsole(campaign, target, frontendUrl);
        const recordEvent = async (eventType) => {
            if (!evidence.value.trim()) {
                evidence.setCustomValidity("Record concrete operator evidence before confirming this state.");
                evidence.reportValidity();
                return;
            }
            evidence.setCustomValidity("");
            message.textContent = `Recording ${eventType} evidence…`;
            try {
                await postCampaignAction(
                    `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/intervention-events`,
                    {
                        targetService: service.targetService,
                        eventType,
                        operatorConfirmed: true,
                        evidence: evidence.value.trim(),
                        occurredAt: new Date().toISOString(),
                    }
                );
                await refresh();
            } catch (error) {
                message.textContent = error.message;
                message.classList.add("is-error");
            }
        };

        if (service.evidenceReady) {
            controls.appendChild(guidedText("span", "intervention-service__complete", "Cohort correlated and service restored."));
        } else if (service.state === "active") {
            const run = interventionActionButton("Run checkout trial", async () => {
                if (!frontendUrl.reportValidity()) return;
                run.disabled = true;
                message.textContent = "Executing checkout under the confirmed intervention…";
                try {
                    await postCampaignAction(
                        `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/interventions/${encodeURIComponent(service.targetService)}/runs`,
                        {frontendUrl: frontendUrl.value.trim()}
                    );
                    await refresh();
                } catch (error) {
                    message.textContent = guidedConnectionError(error);
                    message.classList.add("is-error");
                    run.disabled = false;
                }
            });
            const restore = interventionActionButton("Confirm restored", () => recordEvent("restored"));
            restore.disabled = service.eligibleTrials < service.requiredTrials || service.excludedTrials > 0;
            controls.append(run, evidence, restore);
        } else {
            const activate = interventionActionButton(
                service.state === "restored" ? "Confirm intervention active again" : "Confirm intervention active",
                () => recordEvent("activated")
            );
            controls.append(evidence, activate);
        }
        card.append(title, progress, controls, message);
        return card;
    }

    function renderAssessmentAction(campaign, target) {
        const section = document.createElement("section");
        section.className = "assessment-console";
        const status = guidedText("span", "baseline-console__status", "All service cohorts are ready.");
        const button = interventionActionButton("Classify dependencies", async () => {
            button.disabled = true;
            status.textContent = "Comparing baseline and intervention outcomes…";
            try {
                const assessment = await postCampaignAction(
                    `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/assessment`,
                    {}
                );
                renderCampaignAssessment(campaign, assessment, section);
            } catch (error) {
                status.textContent = error.message;
                status.classList.add("is-error");
                button.disabled = false;
            }
        });
        section.append(
            guidedText("div", "guided-result__label", "04  ·  CLASSIFY"),
            guidedText("h5", "", "Convert controlled outcomes into dependency classes"),
            status,
            button
        );
        return section;
    }

    async function loadCampaignDecision(campaign, target) {
        const base = `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}`;
        try {
            const approval = await requestScenario(`${base}/approval`);
            renderApprovedGraph(approval, target);
            return;
        } catch (error) {
            if (error.status !== 404) {
                target.replaceChildren(guidedText("p", "baseline-console__status is-error", error.message));
                return;
            }
        }
        try {
            const assessment = await requestScenario(`${base}/assessment`);
            renderCampaignAssessment(campaign, assessment, target);
        } catch (error) {
            if (error.status === 404) {
                target.replaceWith(renderAssessmentAction(campaign, target));
                return;
            }
            target.replaceChildren(guidedText("p", "baseline-console__status is-error", error.message));
        }
    }

    function renderCampaignAssessment(campaign, assessment, target) {
        target.replaceChildren(
            guidedText("div", "guided-result__label", "04  ·  CLASSIFICATION RESULT"),
            guidedText("h5", "", "Review the evidence before releasing a graph")
        );
        const results = document.createElement("div");
        results.className = "assessment-console__results";
        assessment.services.forEach((service) => {
            const row = document.createElement("article");
            row.append(
                guidedText("strong", "", service.serviceName),
                guidedText("span", "guided-maturity", service.classification.replaceAll("_", " ")),
                guidedText(
                    "small",
                    "",
                    `Baseline ${Math.round(service.baseline.successRate * 100)}% → intervention ${Math.round(service.intervention.successRate * 100)}% · evidence strength ${Math.round(service.evidenceStrength * 100)}%`
                ),
                guidedText("p", "", service.reasons.join(" "))
            );
            results.appendChild(row);
        });
        const entry = guidedText(
            "p",
            "assessment-console__entry",
            `${assessment.structuralEntryService} is retained as the structural entrypoint; it was not falsely classified through a self-defeating outage.`
        );
        const rationale = document.createElement("textarea");
        rationale.rows = 3;
        rationale.placeholder = "Why is this graph fit to hand to Component 3?";
        rationale.setAttribute("aria-label", "Approval rationale");
        const confirmationLabel = guidedText("label", "assessment-console__confirmation", "");
        const confirmation = document.createElement("input");
        confirmation.type = "checkbox";
        confirmationLabel.append(confirmation, document.createTextNode(" I reviewed every classification and its evidence."));
        const approvalStatus = guidedText("span", "baseline-console__status", "Not approved. Component 3 cannot consume this graph yet.");
        const approve = interventionActionButton("Approve WorkflowGraph", async () => {
            if (!rationale.value.trim() || !confirmation.checked) {
                approvalStatus.textContent = "Add a review rationale and confirm the evidence review.";
                approvalStatus.classList.add("is-error");
                return;
            }
            approve.disabled = true;
            try {
                const approval = await postCampaignAction(
                    `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/approval`,
                    {
                        rationale: rationale.value.trim(),
                        approvalConfirmed: true,
                    }
                );
                renderApprovedGraph(approval, target);
            } catch (error) {
                approvalStatus.textContent = error.message;
                approvalStatus.classList.add("is-error");
                approve.disabled = false;
            }
        });
        target.append(results, entry, rationale, confirmationLabel, approve, approvalStatus);
        updateExperimentPath(4);
    }

    function renderApprovedGraph(approval, target) {
        target.replaceChildren(
            guidedText("div", "guided-result__label", "05  ·  APPROVED OUTPUT"),
            guidedText("h5", "", "WorkflowGraph released to Component 3")
        );
        const graph = approval.approvedGraph;
        const summary = document.createElement("div");
        summary.className = "approved-graph";
        summary.append(
            resultCard("Mandatory", graph.mandatoryServices.join(", ") || "None", "Workflow cannot succeed without these"),
            resultCard("Optional", graph.optionalServices.join(", ") || "None", "No material outcome effect observed"),
            resultCard("Retained edges", String(graph.edges.length), "Only between approved services")
        );
        target.append(
            summary,
            guidedText("p", "assessment-console__entry", `Approved by ${approval.approvedBy}. ${approval.rationale}`)
        );
        updateExperimentPath(5);
        if (dataSourceBadge) dataSourceBadge.textContent = "EXPERIMENT · GRAPH APPROVED";
    }

    async function createGuidedCampaign(session, requiredTrials, button, status) {
        button.disabled = true;
        status.textContent = "Generating baseline and intervention cohorts…";
        try {
            const campaigns = await requestScenario("/api/workflows/experiment-campaigns");
            const existing = campaigns.find(
                (campaign) =>
                    campaign.workflowId === "checkout" &&
                    campaign.environmentId === session.environmentId &&
                    campaign.status !== "approved"
            );
            if (existing) {
                renderCampaign(existing);
                setGuidedStatus(
                    "Existing checkout experiment loaded. No test was run; continue with the normal checkout baseline."
                );
                return;
            }
            const campaign = await requestScenario("/api/workflows/experiment-campaigns", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Actor": "guided-local-researcher",
                },
                body: JSON.stringify({
                    analysisSessionId: session.sessionId,
                    successContractConfirmed: true,
                    requiredBaselineTrials: requiredTrials,
                    benchmarkProfile: "online_boutique_checkout_v0_10_1",
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
            guidedText("strong", "", "What this result means"),
            guidedText(
                "span",
                "",
                `${session.observedServices.length} services appeared while this workflow ran. ` +
                "They participated, but this does not yet prove the workflow needs every one of them."
            )
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
        const isCheckoutBenchmark = supportsGuidedExperiment(session);
        contract.append(
            guidedText("span", "guided-result__label", "Business success contract"),
            guidedText(
                "h5",
                "",
                isCheckoutBenchmark
                    ? "Customer receives a confirmed order"
                    : "Observation complete — experiment not configured"
            ),
            guidedText(
                "p",
                "",
                isCheckoutBenchmark
                    ? "The checkout request completes with HTTP 200 and the checkoutservice records PlaceOrder."
                    : "This workflow can be observed, but the application does not yet know how to run it safely or how to decide that it succeeded. Choose Checkout to continue with the ready-to-run dependency experiment."
            )
        );

        if (!isCheckoutBenchmark) {
            const checkout = guidedWorkflows.find(supportsGuidedExperiment);
            if (checkout) {
                const switchButton = guidedText(
                    "button",
                    "experiment-button",
                    "Continue with Checkout instead"
                );
                switchButton.type = "button";
                switchButton.addEventListener("click", async () => {
                    guidedOperation.value = checkout.operationName;
                    await inspectGuidedWorkflow();
                });
                contract.append(
                    switchButton,
                    guidedText(
                        "small",
                        "guided-contract__status",
                        "Checkout includes a predefined request and a measurable success rule."
                    )
                );
            }
            guidedResult.append(heading, metrics, warning, evidence, contract);
            updateExperimentPath(0);
            if (dataSourceBadge) {
                dataSourceBadge.textContent = `${session.evidenceTier.toUpperCase()} · OBSERVED ONLY`;
                dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
            }
            return;
        }

        const confirmation = document.createElement("label");
        confirmation.className = "guided-contract__confirmation";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        confirmation.append(
            checkbox,
            document.createTextNode(" This correctly defines a successful checkout for this experiment.")
        );
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
        const planStatus = guidedText(
            "small",
            "guided-contract__status",
            "This creates the experiment plan only. No service will be changed yet."
        );
        checkbox.addEventListener("change", () => {
            planButton.disabled = !checkbox.checked;
        });
        planButton.addEventListener("click", () => {
            createGuidedCampaign(session, Number(trials.value), planButton, planStatus);
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

    // ---------------------------------------------------------------------------
    // Automatic discovery of observed workflows
    //
    // The page asks the trace service by itself: a lightweight health check until it answers, then a bounded
    // discovery, then a slower refresh for newly arriving traces (see workflow-discovery-poller.js). The
    // candidates are what was observed. Nothing here classifies, approves or ranks them by business value.
    // ---------------------------------------------------------------------------

    const OBSERVED_STATE_LABELS = {
        waiting: "Waiting for trace service…",
        checking: "Checking trace service…",
        jaeger_unavailable: "Waiting for trace service…",
        no_traces: "Trace service connected · no traces yet",
        no_candidates: "Traces recorded · no business workflows",
        incomplete_traces: "Traces recorded · tracing evidence incomplete",
        discovered: "Observed workflows found",
        error: "Discovery could not run",
    };
    const EVIDENCE_LABELS = {
        complete: "Complete trace evidence",
        incomplete: "Some traces incomplete",
        unusable: "Trace evidence incomplete",
    };
    const LOADING_TEXT = "Loading available workflows…";

    let lastDiscoverySignature = null;
    let resolveFirstDiscovery = () => {};
    let firstDiscovery = Promise.resolve();

    function setGuidedOptions(texts) {
        guidedOperation.replaceChildren();
        texts.forEach(({ value, label }) => {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = label;
            guidedOperation.appendChild(option);
        });
    }

    function clearLoadingText() {
        if (guidedStatus && guidedStatus.textContent === LOADING_TEXT) setGuidedStatus("");
    }

    function showObservedState(state, detail) {
        if (guidedObserved) {
            guidedObserved.hidden = false;
            guidedObserved.dataset.state = state;
        }
        if (guidedObservedState) guidedObservedState.textContent = OBSERVED_STATE_LABELS[state] || state;
        if (guidedObservedDetail) guidedObservedDetail.textContent = detail || "";
        if (guidedObservedChecked) guidedObservedChecked.textContent = `Last checked ${new Date().toLocaleTimeString()}`;
    }

    function showNoWorkflows(label) {
        guidedWorkflows = [];
        setGuidedOptions([{ value: "", label }]);
        if (guidedCandidates) guidedCandidates.replaceChildren();
        updateGuidedSelectionAction();
    }

    function renderCandidate(workflow) {
        const row = document.createElement("li");
        row.className = "guided-candidate";
        row.dataset.evidence = workflow.evidenceStatus || "";
        const head = document.createElement("div");
        head.className = "guided-candidate__head";
        head.append(
            guidedText("strong", "guided-candidate__operation", workflow.operationName),
            guidedText(
                "span",
                "guided-candidate__meta",
                `enters at ${workflow.rootService} · ${workflow.traceCount} trace${workflow.traceCount === 1 ? "" : "s"}`
            )
        );
        const services = document.createElement("div");
        services.className = "guided-candidate__services";
        (workflow.participatingServices || []).forEach((service) => {
            services.appendChild(guidedText("span", "contract-chip", service));
        });
        const evidence = guidedText(
            "small",
            "guided-candidate__evidence",
            `${EVIDENCE_LABELS[workflow.evidenceStatus] || "Evidence not graded"} · observed only, dependency class not determined`
        );
        row.append(head, services, evidence);
        return row;
    }

    function renderDiscoveredWorkflows(report) {
        const previous = guidedOperation.value;
        guidedWorkflows = [...report.workflows];
        setGuidedOptions(
            guidedWorkflows.map((workflow) => ({
                value: workflowKey(workflow),
                label:
                    `${workflow.operationName} — ${supportsGuidedExperiment(workflow) ? "ready for dependency experiment" : "observation only"}` +
                    ` · ${workflow.traceCount} traces · ${workflow.participatingServices.length} services`,
            }))
        );
        // A refresh keeps the operator's choice when that workflow is still there.
        if (guidedWorkflows.some((workflow) => workflowKey(workflow) === previous)) guidedOperation.value = previous;
        if (guidedCandidates) guidedCandidates.replaceChildren(...guidedWorkflows.map(renderCandidate));
        updateGuidedSelectionAction();
    }

    function observedDetail(report) {
        const parts = [];
        if (report.workflows.length) {
            parts.push(
                `${report.workflows.length} workflow${report.workflows.length === 1 ? "" : "s"} from ${report.tracesFetched} traces`
            );
        }
        const scanned = report.servicesScanned.length;
        if (report.entryDetection === "call_graph") {
            // Only services nothing else calls are read; the rest are inside their callers' traces.
            parts.push(
                `read the most recent traces of ${scanned} entry service${scanned === 1 ? "" : "s"} ` +
                `(${report.servicesCalledByOthers.length} more reached through them)`
            );
        } else {
            parts.push(`${scanned} of ${report.servicesRecorded.length} recorded services read (call graph not available)`);
        }
        if (report.servicesUnexamined && report.servicesUnexamined.length) {
            // Never hidden: a service the limits kept from being read, and that no other trace showed.
            parts.push(
                `not examined because of the discovery limit: ${report.servicesUnexamined.join(", ")} ` +
                "(raise WORKFLOW_DISCOVERY_MAX_SERVICES)"
            );
        } else if (report.truncated) {
            parts.push("a sample: the busiest services have more traces in this window than the limit reads");
        }
        if (report.workflows.some((workflow) => workflow.evidenceStatus !== "complete") && report.state === "discovered") {
            parts.push("some trace evidence is incomplete");
        }
        return parts.join(" · ");
    }

    function onDiscoveryReport(report) {
        clearLoadingText();
        const state = report.state;
        if (state !== "jaeger_unavailable" && findSaysDown) {
            setFindStatus("");
            if (startPrompt) startPrompt.hidden = true;
        }
        if (state === "discovered" || state === "incomplete_traces") {
            const signature = JSON.stringify([
                state,
                report.workflows.map((w) => [workflowKey(w), w.traceCount, w.participatingServices, w.evidenceStatus]),
            ]);
            // Unchanged evidence leaves the list and the operator's selection alone.
            if (signature !== lastDiscoverySignature) {
                lastDiscoverySignature = signature;
                renderDiscoveredWorkflows(report);
            }
        } else {
            lastDiscoverySignature = null;
            showNoWorkflows(
                state === "jaeger_unavailable"
                    ? OBSERVED_STATE_LABELS.waiting
                    : state === "no_traces"
                        ? `No traces recorded in the last ${report.lookback} yet`
                        : "No business workflows among the recorded traces"
            );
        }
        let detail =
            state === "discovered" ? observedDetail(report) : `${report.message} ${observedDetail(report)}`.trim();
        if (state === "no_traces" && report.servicesRecorded.length === 0) {
            // A running trace service is only the place traces are stored; an application has to send them.
            detail += " Nothing has sent traces to it yet: an application must send them (OTLP, port 4317 or 4318).";
        }
        showObservedState(state, detail);
        resolveFirstDiscovery();
    }

    function onDiscoveryWaiting() {
        clearLoadingText();
        lastDiscoverySignature = null;
        showNoWorkflows(OBSERVED_STATE_LABELS.waiting);
        showObservedState(
            "waiting",
            "Checking again every 10 seconds. Workflows load here by themselves once the trace service answers; " +
                "the address is under Advanced connection settings."
        );
        resolveFirstDiscovery();
    }

    function onDiscoveryError(error) {
        clearLoadingText();
        lastDiscoverySignature = null;
        showNoWorkflows("No workflows available — check advanced settings");
        const rejected = Boolean(error && error.status === 422);
        // A rejected setting is explained as the server wrote it; only a failed connection gets the generic text.
        showObservedState("error", rejected ? error.message : guidedConnectionError(error));
        if (rejected) {
            // A rejected address or setting will not fix itself; changing it starts discovery again.
            discoveryPoller.stop();
        }
        resolveFirstDiscovery();
    }

    // True when the last health check was refused by the server (for example a host that is not approved).
    let healthRefused = false;

    async function checkTraceServiceHealth() {
        const address = guidedAddress ? guidedAddress.value.trim() : "";
        healthRefused = false;
        try {
            const status = await fetchJson(
                `/api/workflows/jaeger/status${address ? `?jaeger_url=${encodeURIComponent(address)}` : ""}`
            );
            return Boolean(status.healthy);
        } catch (error) {
            if (error && error.status === 422) {
                // The address was refused (for example, a host that is not approved). Waiting will not help:
                // say why, and stop until the address changes.
                healthRefused = true;
                onDiscoveryError(error);
            }
            return false;
        }
    }

    function discoverObservedWorkflows() {
        const connection = guidedConnectionPayload();
        return requestScenario("/api/workflows/discovery", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jaegerUrl: connection.jaegerUrl, lookback: connection.lookback }),
        });
    }

    const discoveryPoller = window.WorkflowDiscoveryPoller.create({
        checkHealth: checkTraceServiceHealth,
        discover: discoverObservedWorkflows,
        onChecking: () => {
            if (guidedObserved && guidedObserved.hidden) showObservedState("checking", "");
        },
        onWaiting: onDiscoveryWaiting,
        onReport: onDiscoveryReport,
        onError: onDiscoveryError,
        isHidden: () => Boolean(document.hidden),
    });

    // ---------------------------------------------------------------------------
    // "Find workflows": one button that checks the trace service, offers to start it, and shows what it recorded.
    //
    // Starting the trace service means running a container on this computer, so it only ever happens after
    // the person says yes in the prompt below, and only through the extension's host helper (a web page cannot
    // start a container). Without the helper the page says how to start it by hand. It never starts an
    // application, sends traffic or changes a Kubernetes workload.
    // ---------------------------------------------------------------------------

    const MANUAL_START =
        "The trace service is not running. Start it yourself with: docker compose -f deploy/docker-compose.jaeger.yml up -d. " +
        "This page then finds it by itself.";
    let findBusy = false;
    // True while the line under the button says the trace service is not running; it is cleared as soon as a
    // report shows the service answering, so the page never says "not running" about a running service.
    let findSaysDown = false;

    const traceService = () => window.__resilienceTraceService || { available: false };

    function setFindStatus(message, isError = false, saysDown = false) {
        findSaysDown = saysDown;
        if (!findStatus) return;
        findStatus.textContent = message;
        findStatus.style.color = isError ? "var(--color-danger, #b91c1c)" : "var(--color-text-muted)";
    }

    function setFindBusy(busy) {
        findBusy = busy;
        [findButton, startConfirm, startCancel, stopButton].forEach((button) => {
            if (button) button.disabled = busy;
        });
    }

    // The Stop button is offered only for a trace service the extension started itself.
    function showStopFor(state) {
        if (stopButton) stopButton.hidden = state !== "trace-running";
    }

    async function findWorkflows() {
        if (findBusy || !guidedForm) return;
        if (!guidedForm.reportValidity()) {
            reportMissingAddress();
            return;
        }
        setFindBusy(true);
        if (startPrompt) startPrompt.hidden = true;
        setFindStatus("Checking the trace service…");
        try {
            if (await checkTraceServiceHealth()) {
                setFindStatus("The trace service is running. Looking for workflows…");
                discoveryPoller.refresh();
                const known = traceService().available ? await traceService().status() : null;
                if (known) showStopFor(known.state);
                return;
            }
            if (healthRefused) {
                setFindStatus(""); // the panel above already says why the address was refused
                return;
            }
            discoveryPoller.start(); // keeps watching, so it shows the result as soon as the service answers
            if (traceService().available) {
                setFindStatus("The trace service is not running.", false, true);
                if (startPrompt) startPrompt.hidden = false;
            } else {
                setFindStatus(MANUAL_START, false, true);
            }
        } finally {
            setFindBusy(false);
        }
    }

    async function startTraceService() {
        if (findBusy || !traceService().available) return;
        setFindBusy(true);
        if (startPrompt) startPrompt.hidden = true;
        setFindStatus("Starting the trace service… the first time it may need to download the image.");
        try {
            const result = await traceService().start();
            showStopFor(result.state);
            if (!result.ok) {
                setFindStatus(result.message, true);
                return;
            }
            setFindStatus(`${result.message} Looking for workflows…`);
            discoveryPoller.refresh();
        } finally {
            setFindBusy(false);
        }
    }

    function declineTraceStart() {
        if (startPrompt) startPrompt.hidden = true;
        setFindStatus("Nothing was started.");
    }

    async function stopTraceService() {
        if (findBusy || !traceService().available) return;
        setFindBusy(true);
        setFindStatus("Stopping the trace service…");
        try {
            const result = await traceService().stop();
            showStopFor(result.state === "trace-stopped" ? "" : "trace-running");
            setFindStatus(result.message, !result.ok);
            discoveryPoller.refresh();
        } finally {
            setFindBusy(false);
        }
    }

    // On page load, learn whether a trace service of ours is already running, to offer Stop.
    async function showStopIfOurs() {
        if (!traceService().available) return;
        try {
            showStopFor((await traceService().status()).state);
        } catch (_error) {
            showStopFor("");
        }
    }

    function reportMissingAddress() {
        // The page starts in a "Loading…" state. Returning silently would leave it there forever, so say what
        // is missing and put the controls back.
        showNoWorkflows("Enter the trace service address to load workflows");
        setGuidedStatus("Enter the workflow trace service address (under Advanced connection settings) to load workflows.", true);
        resolveFirstDiscovery();
    }

    // Starts, or immediately repeats, discovery. Used on page open, by the refresh button, and when the
    // address or time window changes.
    function discoverGuidedWorkflows(event) {
        if (event) event.preventDefault();
        if (!guidedForm) return;
        if (!guidedForm.reportValidity()) {
            discoveryPoller.stop();
            reportMissingAddress();
            return;
        }
        setGuidedStatus("");
        discoveryPoller.refresh();
    }

    // The first discovery result (or its failure) is awaited so the previous experiment loads after it, as before.
    function startGuidedDiscovery() {
        firstDiscovery = new Promise((resolve) => { resolveFirstDiscovery = resolve; });
        discoverGuidedWorkflows();
        return firstDiscovery;
    }

    async function inspectGuidedWorkflow() {
        if (!guidedOperation || !guidedOperation.value) return;
        guidedInspectButton.disabled = true;
        const selected = selectedGuidedWorkflow();
        if (!selected) return;
        setGuidedStatus(
            supportsGuidedExperiment(selected)
                ? "Opening the checkout dependency experiment…"
                : "Preparing the observed-service summary…"
        );
        try {
            if (supportsGuidedExperiment(selected)) {
                const campaigns = await requestScenario("/api/workflows/experiment-campaigns");
                const existing = campaigns.find(
                    (campaign) =>
                        campaign.workflowId === "checkout" &&
                        campaign.environmentId === guidedConnectionPayload().environmentId
                );
                if (existing) {
                    renderCampaign(existing);
                    setGuidedStatus(
                        "Checkout experiment opened. Continue with the highlighted step below."
                    );
                    return;
                }
            }
            const session = await requestScenario("/api/workflows/analysis-sessions", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    "X-Actor": "guided-local-operator",
                },
                body: JSON.stringify({
                    ...guidedConnectionPayload(),
                    entryService: selected.rootService,
                    operationName: selected.operationName,
                }),
            });
            renderGuidedSession(session);
            setGuidedStatus("Observation recorded. No dependency-necessity claim has been made.");
        } catch (error) {
            setGuidedStatus(error.message, true);
        } finally {
            updateGuidedSelectionAction();
        }
    }

    async function loadLatestExperiment() {
        if (!guidedResult) return;
        try {
            const campaigns = await requestScenario("/api/workflows/experiment-campaigns");
            const completedCampaign = campaigns.find(
                (campaign) => campaign.status === "approved"
            );
            if (completedCampaign) {
                renderCampaign(completedCampaign);
                return;
            }
            const incompleteCampaign = campaigns.find(
                (campaign) => campaign.status !== "approved"
            );
            if (incompleteCampaign) {
                renderCampaign(incompleteCampaign);
                setGuidedStatus(
                    "Existing checkout experiment loaded. No test was run automatically. Continue by running the normal checkout baseline."
                );
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
            (item) => workflowKey(item) === discoveredSelect.value
        );
        if (!candidate) return;
        const services = candidate.participatingServices || [];
        // A typed entry service overrides; blank means the service the traces show the request entering at.
        const entryService = (entryServiceInput && entryServiceInput.value.trim()) || candidate.rootService || "";
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
            renderDependencyAssessments(
                await requestScenario("/api/workflows/dependency-assessments")
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
        setAssessmentStatus(
            "Creating an assessment needs a candidate graph built from live trace evidence. The synthetic " +
            "fixture graph it used to start from is no longer part of the application.",
            true
        );
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
            dataSourceBadge.textContent = "Jaeger • origin unverified";
            dataSourceBadge.className = isLive
                ? "status-badge status-badge--success"
                : "status-badge status-badge--muted status-badge--no-dot";
        }

        updateLastRefreshed(lastRefreshed);
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = 'Jaeger • origin unverified';
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
        }
    }


    // The address the server was set up to use, filled into the address fields that are still empty. It is
    // never hard-coded here: "localhost" means the container itself when the backend runs inside Docker.
    // "&jaeger_url=..." only when an address was typed, so an empty field means "the server's own setting".
    function jaegerQuery(address) {
        return address ? `&jaeger_url=${encodeURIComponent(address)}` : "";
    }

    function fillJaegerDefault(address) {
        const guided = document.getElementById("guided-jaeger-url");
        [jaegerUrlInput, guided].forEach((input) => {
            if (input && address && !input.value.trim()) input.value = address;
        });
    }

    // The address fields start empty, because the right address depends on where the backend runs. Ask the server
    // which one it uses before anything needs it; if that fails the fields stay empty and the page says so.
    async function ensureJaegerDefault() {
        try {
            const res = await fetchJson("/api/workflows/jaeger/status");
            fillJaegerDefault(res.jaegerUrl);
        } catch (_error) {
            // Nothing to fill in; discoverGuidedWorkflows reports the missing address.
        }
    }

    async function checkJaegerHealth() {
        if (!jaegerStatusBadge || !jaegerUrlInput) return;
        try {
            // With an empty field no address is sent, so the server checks the one it was set up with.
            const typed = jaegerUrlInput.value.trim();
            const url = `/api/workflows/jaeger/status${typed ? `?jaeger_url=${encodeURIComponent(typed)}` : ""}`;
            const res = await fetchJson(url);
            fillJaegerDefault(res.jaegerUrl);
            if (res.healthy) {
                jaegerStatusBadge.textContent = "Trace service connected";
                jaegerStatusBadge.className = "status-badge status-badge--success";
            } else {
                jaegerStatusBadge.textContent = "Trace service unavailable";
                jaegerStatusBadge.className = "status-badge status-badge--danger";
            }
        } catch (err) {
            const rejected = Boolean(err && err.status === 422);
            jaegerStatusBadge.textContent = rejected ? "Address not approved" : "Trace service unavailable";
            jaegerStatusBadge.title = rejected ? err.message : "";
            jaegerStatusBadge.className = "status-badge status-badge--danger";
        }
    }

    // A message under the buttons, never a browser alert: an alert blocks the whole page (and with it the
    // automatic checking) until it is dismissed, and its text cannot be selected.
    function setLiveConfigStatus(message, isError = false) {
        if (!liveConfigStatus) return;
        liveConfigStatus.textContent = message;
        liveConfigStatus.style.color = isError ? "var(--color-danger, #b91c1c)" : "var(--color-text-muted)";
    }

    async function handleDiscoverWorkflows() {
        if (!discoverButton) return;
        discoverButton.disabled = true;
        discoverButton.textContent = "Discovering…";
        setLiveConfigStatus("");

        const jaegerUrl = jaegerUrlInput ? jaegerUrlInput.value.trim() : "";
        const serviceName = entryServiceInput ? entryServiceInput.value.trim() : "";
        const lookback = lookbackSelect ? lookbackSelect.value : "1h";

        try {
            if (serviceName) {
                const url = `/api/workflows/discover?limit=1000&service_name=${encodeURIComponent(serviceName)}${jaegerQuery(jaegerUrl)}&lookback=${encodeURIComponent(lookback)}`;
                discoveredWorkflows = await fetchJson(url);
            } else {
                // No entry service named: find the entry services from the recorded traces, within the bounds.
                const report = await requestScenario("/api/workflows/discovery", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ jaegerUrl, lookback, limit: 1000 }),
                });
                if (report.state === "jaeger_unavailable") throw new Error(report.message);
                discoveredWorkflows = report.workflows;
            }

            if (discoveredSelect && discoveredContainer) {
                discoveredSelect.innerHTML = "";
                if (discoveredWorkflows.length > 0) {
                    discoveredWorkflows.forEach((w) => {
                        const opt = document.createElement("option");
                        opt.value = workflowKey(w);
                        opt.textContent = `${w.operationName} · enters at ${w.rootService} (${w.traceCount} traces, ${w.participatingServices.length} services)`;
                        discoveredSelect.appendChild(opt);
                    });
                    discoveredContainer.style.display = "block";
                    if (extractLiveButton) extractLiveButton.disabled = false;
                    const found = discoveredWorkflows.length;
                    setLiveConfigStatus(`Found ${found} candidate operation${found === 1 ? "" : "s"}.`);
                } else {
                    const opt = document.createElement("option");
                    opt.value = "";
                    opt.textContent = "No workflows observed in this time window yet";
                    discoveredSelect.appendChild(opt);
                    discoveredContainer.style.display = "block";
                    if (extractLiveButton) extractLiveButton.disabled = true;
                }
            }
        } catch (error) {
            setLiveConfigStatus(`Could not discover workflows: ${error.message}`, true);
        } finally {
            discoverButton.disabled = false;
            discoverButton.textContent = "Discover Candidate Operations";
        }
    }

    async function handleExtractLive() {
        if (!extractLiveButton) return;
        const candidate = discoveredSelect
            ? discoveredWorkflows.find((item) => workflowKey(item) === discoveredSelect.value)
            : undefined;
        if (!candidate) {
            setLiveConfigStatus("Select a candidate operation from the dropdown first.", true);
            return;
        }
        const operation = candidate.operationName;

        extractLiveButton.disabled = true;
        extractLiveButton.textContent = "Extracting…";
        setLiveConfigStatus("");

        const jaegerUrl = jaegerUrlInput ? jaegerUrlInput.value.trim() : "";
        const serviceName = (entryServiceInput && entryServiceInput.value.trim()) || candidate.rootService || "";
        const lookback = lookbackSelect ? lookbackSelect.value : "1h";

        try {
            const url = `/api/workflows/live/evidence?service_name=${encodeURIComponent(serviceName)}&operation=${encodeURIComponent(operation)}${jaegerQuery(jaegerUrl)}&lookback=${encodeURIComponent(lookback)}`;
            const workflow = await fetchJson(url);
            renderWorkflowData(workflow, true);
        } catch (error) {
            setLiveConfigStatus(`Live extraction failed: ${error.message}`, true);
        } finally {
            extractLiveButton.disabled = false;
            extractLiveButton.textContent = "Extract Live Dependency Graph";
        }
    }




    // ---------------------------------------------------------------------------
    // Event wiring
    // ---------------------------------------------------------------------------

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
    if (guidedOperation) guidedOperation.addEventListener("change", updateGuidedSelectionAction);
    if (findButton) findButton.addEventListener("click", findWorkflows);
    if (startConfirm) startConfirm.addEventListener("click", startTraceService);
    if (startCancel) startCancel.addEventListener("click", declineTraceStart);
    if (stopButton) stopButton.addEventListener("click", stopTraceService);
    // A new address or time window asks again straight away; the poller keeps requests from overlapping.
    if (guidedAddress) guidedAddress.addEventListener("change", discoverGuidedWorkflows);
    if (guidedLookback) guidedLookback.addEventListener("change", discoverGuidedWorkflows);
    // Nothing is requested while the tab is hidden; coming back to it (or focusing the window) refreshes once.
    document.addEventListener("visibilitychange", () => discoveryPoller.pageVisibilityChanged());
    if (typeof window.addEventListener === "function") {
        window.addEventListener("focus", () => discoveryPoller.pageVisibilityChanged());
    }
    if (researchAudit) {
        researchAudit.addEventListener("toggle", () => {
            if (!researchAudit.open || researchAuditInitialized) return;
            researchAuditInitialized = true;
            checkJaegerHealth();
            loadScenarioRegistry();
            loadExecutionLedger();
            loadDependencyAssessments();
            loadClassificationLab();
        });
    }
    stageButtons.forEach((button) => {
        button.addEventListener("click", () => {
            activateWorkflowStage(button.dataset.workflowStageButton);
        });
    });

    document.addEventListener("DOMContentLoaded", () => {
        activateWorkflowStage("define");
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = "No active campaign";
        showStopIfOurs();
        ensureJaegerDefault().then(startGuidedDiscovery).then(loadLatestExperiment);
    });
})();
