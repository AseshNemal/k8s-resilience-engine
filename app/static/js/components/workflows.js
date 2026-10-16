/*
 * Workflow Dependency Modeling page script.
 * Owned by: Member 1 (A.D. Perera).
 *
 * Supports sample data and live Jaeger, Zipkin, or Tempo trace discovery.
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
    const telemetryReadinessForm = document.querySelector('[data-role="telemetry-readiness-form"]');
    const telemetrySourceType = document.querySelector('[data-role="telemetry-source-type"]');
    const telemetryEndpoint = document.querySelector('[data-role="telemetry-endpoint"]');
    const telemetryReadinessResult = document.querySelector('[data-role="telemetry-readiness-result"]');
    const guidedTraceSource = document.querySelector('[data-role="guided-trace-source"]');
    const guidedService = guidedForm?.elements.namedItem("entryService");
    const guidedServiceField = document.querySelector('[data-role="guided-service-field"]');

    let discoveredWorkflows = [];
    let guidedWorkflows = [];
    let guidedConnectionVersion = 0;
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

    function telemetryList(title, values) {
        const section = document.createElement("div");
        section.className = "guided-next";
        section.appendChild(guidedText("h5", "", title));
        const list = document.createElement("ul");
        (values || []).forEach((value) => {
            list.appendChild(guidedText("li", "", value));
        });
        if (!list.childNodes.length) {
            list.appendChild(guidedText("li", "", "None"));
        }
        section.appendChild(list);
        return section;
    }

    function renderTelemetryReadiness(readiness) {
        if (!telemetryReadinessResult) return;
        const summary = document.createElement("div");
        summary.className = "approved-graph";
        summary.append(
            resultCard("Evidence level", `${readiness.evidenceLevel} · ${readiness.evidenceLabel}`, "Maximum defensible evidence from this source"),
            resultCard("Adapter", readiness.adapterStatus.replaceAll("_", " "), "Current Component 1 implementation status"),
            resultCard("Candidate graph", readiness.readyForCandidateDiscovery ? "Ready" : "Blocked", "Observed or source-expected candidates"),
            resultCard("Causal graph", readiness.readyForCausalGraph ? "Ready" : "Blocked", "Requires parent-linked distributed traces")
        );
        const classification = readiness.readyForValidatedClassification
            ? "Validated classification evidence is ready."
            : "Mandatory, supportive, and optional labels still require controlled outcome interventions.";
        telemetryReadinessResult.replaceChildren(
            summary,
            guidedText("p", "assessment-console__entry", classification),
            telemetryList("Blockers", readiness.blockers),
            telemetryList("Next actions", readiness.nextActions)
        );
        if (dataSourceBadge) {
            dataSourceBadge.textContent = `${readiness.evidenceLabel} · ${readiness.adapterStatus}`;
        }
    }

    function renderTelemetryInspection(inspection) {
        if (!telemetryReadinessResult) return;
        const edges = inspection.edges || [];
        const workflows = inspection.discoveredWorkflows || [];
        const details = document.createElement("div");
        details.className = "telemetry-inspection";
        details.appendChild(guidedText(
            "h5",
            "",
            inspection.causal ? "Imported causal trace inspection" : "Imported non-causal candidate inspection"
        ));
        const summary = document.createElement("div");
        summary.className = "approved-graph";
        summary.append(
            resultCard("Imported records", String(inspection.traceCount ?? inspection.edgeCount ?? 0), inspection.evidenceLabel),
            resultCard("Eligible traces", String(inspection.eligibleTraceCount ?? "Not applicable"), inspection.causal ? "Passed structural quality" : "No trace claim"),
            resultCard("Candidate edges", String(edges.length), inspection.causal ? "Parent-linked observations" : "Explicitly non-causal observations")
        );
        details.appendChild(summary);
        if (inspection.observedServices) {
            details.appendChild(telemetryList("Observed services", inspection.observedServices));
        }
        details.appendChild(telemetryList(
            "Candidate edges",
            edges.map((edge) => `${edge.source} → ${edge.target}${edge.frequency === undefined ? "" : ` · frequency ${edge.frequency.toFixed(3)}`}`)
        ));
        if (workflows.length) {
            details.appendChild(telemetryList(
                "Discovered operations",
                workflows.map((workflow) => `${workflow.operationName} · ${workflow.traceCount} trace(s)`)
            ));
        }
        telemetryReadinessResult.appendChild(details);
    }

    async function assessTelemetryReadiness(event) {
        event.preventDefault();
        if (!telemetryReadinessForm || !telemetryReadinessResult) return;
        const values = new FormData(telemetryReadinessForm);
        const sourceType = String(values.get("sourceType"));
        const payload = {
            sourceType,
            entryService: String(values.get("entryService") || "").trim(),
            hasServiceIdentities: values.has("hasServiceIdentities"),
            hasTimestamps: values.has("hasTimestamps"),
            hasCorrelationIds: values.has("hasCorrelationIds"),
            hasParentRelationships: values.has("hasParentRelationships"),
            hasOutcomeStatus: values.has("hasOutcomeStatus"),
        };
        const endpoint = String(values.get("endpoint") || "").trim();
        if (endpoint) payload.endpoint = endpoint;
        const authenticationType = String(values.get("authentication") || "none");
        payload.authentication = { type: authenticationType };
        if (authenticationType === "bearer_env") {
            payload.authentication.secretEnv = String(values.get("secretEnv") || "").trim();
        }
        const rawJson = String(values.get("offlineJson") || "").trim();
        let importedPayload = null;
        if (rawJson) {
            try {
                importedPayload = JSON.parse(rawJson);
            } catch (error) {
                telemetryReadinessResult.textContent = `Evidence JSON is invalid: ${error.message}`;
                return;
            }
        }
        if (
            importedPayload !== null &&
            ["offline_jaeger", "zipkin", "tempo", "vendor_tracing"].includes(sourceType)
        ) payload.traces = importedPayload;
        if (["offline_jaeger", "vendor_tracing"].includes(sourceType) && importedPayload === null) {
            telemetryReadinessResult.textContent = "This source requires an evidence JSON export.";
            return;
        }
        telemetryReadinessResult.textContent = "Assessing the declared evidence source…";
        const submit = telemetryReadinessForm.querySelector('button[type="submit"]');
        if (submit) submit.disabled = true;
        telemetryReadinessResult.setAttribute("aria-busy", "true");
        try {
            const readiness = await requestScenario("/api/workflows/telemetry/readiness", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            renderTelemetryReadiness(readiness);
            if (
                importedPayload !== null &&
                ["offline_jaeger", "zipkin", "tempo", "vendor_tracing", "correlated_logs", "network_observations", "source_model"].includes(sourceType)
            ) {
                const inspection = await requestScenario("/api/workflows/telemetry/inspect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ sourceType, payload: importedPayload }),
                });
                renderTelemetryInspection(inspection);
            }
            syncGuidedTraceConnection(sourceType, endpoint);
            const guidedEntry = guidedForm?.elements.namedItem("entryService");
            if (guidedEntry) {
                guidedEntry.value = String(values.get("entryService") || "").trim();
            }
            if (["jaeger", "zipkin", "tempo"].includes(sourceType) && guidedForm) {
                guidedForm.elements.namedItem("traceAuthentication").value = authenticationType;
                guidedForm.elements.namedItem("traceSecretEnv").value = String(values.get("secretEnv") || "").trim();
            }
        } catch (error) {
            telemetryReadinessResult.textContent = `Readiness assessment failed: ${error.message}`;
        } finally {
            if (submit) submit.disabled = false;
            telemetryReadinessResult.setAttribute("aria-busy", "false");
        }
    }

    async function loadTelemetrySourceCapabilities() {
        if (!telemetrySourceType) return;
        try {
            const capabilities = await requestScenario("/api/workflows/telemetry/source-types");
            const byType = new Map(capabilities.map((item) => [item.sourceType, item]));
            Array.from(telemetrySourceType.options).forEach((option) => {
                const capability = byType.get(option.value);
                if (!capability || option.dataset.statusApplied) return;
                option.textContent = `${option.textContent} · ${capability.adapterStatus.replaceAll("_", " ")}`;
                option.dataset.statusApplied = "true";
            });
        } catch (error) {
            telemetryReadinessResult.textContent = `Source capabilities unavailable: ${error.message}`;
        }
    }

    function supportsGuidedExperiment(workflow) {
        return Boolean(workflow);
    }

    function supportsObservedEntryProbe(workflow) {
        return /^(GET|POST|PUT|PATCH|DELETE)\s+\/\S*$/i.test(
            String(workflow?.operationName || "").trim()
        );
    }

    function updateGuidedSelectionAction() {
        if (!guidedOperation || !guidedInspectButton) return;
        const selected = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
        guidedInspectButton.disabled = !selected;
        guidedInspectButton.textContent = !selected
            ? "No workflow available"
            : supportsGuidedExperiment(selected)
                ? "View calls and set success"
                : "View service calls";
    }

    function selectedGuidedWorkflow(workflows, selection) {
        return /^\d+$/.test(selection) ? workflows[Number(selection)] : undefined;
    }

    function guidedConnectionPayload() {
        const values = new FormData(guidedForm);
        const authenticationType = String(values.get("traceAuthentication"));
        const authentication = { type: authenticationType };
        if (authenticationType === "bearer_env") {
            authentication.secretEnv = String(values.get("traceSecretEnv") || "").trim();
        }
        return {
            environmentId: String(values.get("environmentId")).trim(),
            evidenceTier: String(values.get("evidenceTier")),
            traceSource: {
                type: String(values.get("traceSourceType")),
                endpoint: String(values.get("traceEndpoint")).trim(),
                authentication,
            },
            entryService: String(values.get("entryService") || "").trim(),
            lookback: String(values.get("lookback")),
        };
    }

    function traceEndpointDefault(sourceType) {
        return {
            jaeger: "http://localhost:16686",
            zipkin: "http://localhost:9411",
            tempo: "http://localhost:3200",
        }[sourceType] || "";
    }

    function resetGuidedDiscovery() {
        guidedConnectionVersion += 1;
        guidedWorkflows = [];
        if (guidedService) {
            guidedService.replaceChildren(new Option("Connect to load recorded services", ""));
            guidedService.disabled = true;
        }
        if (guidedServiceField) guidedServiceField.hidden = true;
        if (guidedDiscovery) guidedDiscovery.hidden = true;
        if (guidedResult) {
            guidedResult.hidden = true;
            guidedResult.replaceChildren();
        }
        if (guidedInspectButton) guidedInspectButton.disabled = true;
        const button = guidedForm?.querySelector('[data-role="guided-discover-button"]');
        if (button) button.disabled = false;
    }

    function syncGuidedAuthentication() {
        if (!guidedForm) return;
        const needsToken = guidedForm.elements.namedItem("traceAuthentication").value === "bearer_env";
        const token = guidedForm.elements.namedItem("traceSecretEnv");
        token.disabled = !needsToken;
        token.required = needsToken;
        document.querySelector('[data-role="guided-secret-field"]').hidden = !needsToken;
    }

    async function connectGuidedSource(event) {
        if (event) event.preventDefault();
        if (!guidedForm || !guidedForm.reportValidity()) return;
        resetGuidedDiscovery();
        const version = guidedConnectionVersion;
        const button = guidedForm.querySelector('[data-role="guided-discover-button"]');
        button.disabled = true;
        setGuidedStatus("Connecting and reading the services already recorded by your tracing backend…");
        try {
            const services = await requestScenario("/api/workflows/analysis-sessions/services", {
                method: "POST",
                headers: {"Content-Type": "application/json"},
                body: JSON.stringify(guidedConnectionPayload()),
            });
            if (version !== guidedConnectionVersion) return;
            guidedService.replaceChildren(new Option(
                services.length ? "Choose a recorded service" : "No recorded services found", ""
            ));
            services.forEach((name) => guidedService.appendChild(new Option(name, name)));
            guidedService.disabled = !services.length;
            guidedServiceField.hidden = false;
            setGuidedStatus(services.length
                ? "Connected. Choose the service that receives your workflow; no service names need to be typed."
                : "Connected, but no services were returned. Check the trace source and its query permissions.");
            if (services.length === 1) {
                guidedService.value = services[0];
                await discoverGuidedWorkflows();
            } else if (services.length) {
                guidedService.focus();
            }
        } catch (error) {
            if (version === guidedConnectionVersion) setGuidedStatus(guidedConnectionError(error), true);
        } finally {
            if (version === guidedConnectionVersion) button.disabled = false;
        }
    }

    function syncGuidedTraceConnection(sourceType, endpoint = "") {
        if (!guidedForm || !["jaeger", "zipkin", "tempo"].includes(sourceType)) return;
        const sourceInput = guidedForm.elements.namedItem("traceSourceType");
        const endpointInput = guidedForm.elements.namedItem("traceEndpoint");
        if (sourceInput) sourceInput.value = sourceType;
        if (endpointInput) endpointInput.value = endpoint || traceEndpointDefault(sourceType);
        resetGuidedDiscovery();
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
        if (message.includes("Could not fetch traces") || message.includes("telemetry backend") || message.includes("query API")) {
            return "The selected trace query service is unavailable. Verify its query URL, authentication, network route, and that trace data exists in the selected time window.";
        }
        return message;
    }

    function guidedText(tag, className, value) {
        const element = document.createElement(tag);
        if (className) element.className = className;
        element.textContent = value;
        return element;
    }

    function downloadJsonArtifact(filename, value) {
        const blob = new Blob([JSON.stringify(value, null, 2)], {
            type: "application/json",
        });
        const link = document.createElement("a");
        link.href = URL.createObjectURL(blob);
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(link.href);
    }

    function updateExperimentPath(activeIndex) {
        const path = document.querySelector(".experiment-path");
        if (path) path.hidden = false;
        document.querySelectorAll(".experiment-path li").forEach((item, index) => {
            item.classList.toggle("is-current", index === activeIndex);
            item.classList.toggle("is-complete", index < activeIndex);
        });
    }

    function observedGraphModel(session) {
        return {
            observedServices: [...session.observedServices],
            edges: session.edges.map(({source, target}) => ({source, target})),
            mandatoryServices: [],
            optionalServices: [],
        };
    }

    function renderObservedGraph(session) {
        const section = document.createElement("section");
        section.className = "guided-observed-graph";
        section.setAttribute("aria-label", "Observed service call graph");
        section.append(
            guidedText("h5", "", "Observed service call graph"),
            guidedText("p", "", "Arrows are calls found in matching traces. They do not establish that a service is required for success.")
        );
        const canvas = document.createElement("div");
        renderGraph(canvas, observedGraphModel(session));
        section.appendChild(canvas);
        const edgeList = document.createElement("details");
        edgeList.className = "guided-observed-graph__edges";
        edgeList.appendChild(guidedText("summary", "", `${session.edges.length} observed call relationships`));
        const list = document.createElement("ul");
        session.edges.forEach(({source, target}) => {
            list.appendChild(guidedText("li", "", `${source} → ${target}`));
        });
        edgeList.appendChild(list);
        section.appendChild(edgeList);
        return section;
    }

    function renderObservedGraphDisclosure(session) {
        const details = document.createElement("details");
        details.className = "guided-observed-graph-disclosure";
        details.append(
            guidedText("summary", "", `View recorded calls · ${session.observedServices.length} services · ${session.edges.length} arrows`),
            renderObservedGraph(session)
        );
        return details;
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
            guidedText("span", "guided-result__label", "Workflow test"),
            guidedText("h4", "", campaign.workflowName),
            guidedText(
                "p",
                "",
                `${campaign.environmentId} · Run the workflow normally, then test one service at a time.`
            )
        );
        heading.append(
            identity,
            guidedText(
                "span",
                "guided-maturity",
                "Checking run status…"
            )
        );

        const metrics = document.createElement("div");
        metrics.className = "guided-result__metrics";
        metrics.append(
            resultCard("Normal runs", `${campaign.baseline.requiredTrials} needed`, "Confirm the workflow succeeds"),
            resultCard("Services to test", String(campaign.candidateServices.length), "One at a time"),
            resultCard("Recorded traces", `${campaign.observedTraceCount}`, "Calls found before testing")
        );

        const contract = document.createElement("section");
        contract.className = "guided-contract guided-contract--confirmed";
        contract.append(
            guidedText("span", "guided-result__label", "Success check"),
            guidedText("h5", "", campaign.successContract.outcome),
            guidedText(
                "p",
                "",
                `${campaign.successContract.condition.terminalService} · ${campaign.successContract.condition.operationName}`
            )
        );

        const manifestGate = document.createElement("section");
        manifestGate.className = "manifest-gate";
        manifestGate.setAttribute("aria-live", "polite");

        const baseline = document.createElement("section");
        baseline.className = "baseline-console";
        baseline.dataset.role = "baseline-console";
        const baselineHeading = document.createElement("div");
        baselineHeading.className = "baseline-console__heading";
        baselineHeading.append(
            guidedText("div", "", "STEP 2 OF 5  ·  CONFIRM NORMAL BEHAVIOR"),
            guidedText("strong", "", `Run ${campaign.baseline.requiredTrials} normal workflow executions before testing a dependency`)
        );
        const baselineControls = document.createElement("div");
        baselineControls.className = "baseline-console__controls";
        const frontendLabel = guidedText("label", "", "Application base address");
        const frontendUrl = document.createElement("input");
        frontendUrl.type = "url";
        frontendUrl.value = "http://localhost:8080";
        frontendUrl.required = true;
        frontendLabel.appendChild(frontendUrl);
        const runButton = guidedText("button", "experiment-button", "Run one baseline execution");
        runButton.type = "button";
        runButton.disabled = true;
        const runStatus = guidedText("span", "baseline-console__status", "Freeze the reproducibility record first.");
        baselineControls.append(frontendLabel, runButton, runStatus);
        const readiness = document.createElement("div");
        readiness.className = "baseline-readiness";
        baseline.append(baselineHeading, baselineControls, readiness);
        runButton.addEventListener("click", async () => {
            if (!frontendUrl.reportValidity()) return;
            runButton.disabled = true;
        runStatus.textContent = "Running one workflow and matching it to its exact trace record…";
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
                        body: JSON.stringify({applicationUrl: frontendUrl.value.trim()}),
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

        const interventionConsole = document.createElement("section");
        interventionConsole.className = "intervention-console";
        interventionConsole.hidden = true;

        const next = document.createElement("div");
        next.className = "guided-next";
        next.appendChild(guidedText("h5", "", "Next action"));
        const blockers = document.createElement("ol");
        blockers.appendChild(guidedText("li", "", "Checking current evidence…"));
        next.appendChild(blockers);

        const observedGraph = document.createElement("section");
        observedGraph.className = "guided-observed-graph-loading";
        observedGraph.textContent = "Loading the recorded call graph…";
        guidedResult.append(heading, metrics, observedGraph, contract, manifestGate, baseline, interventionConsole, next);
        requestScenario(`/api/workflows/analysis-sessions/${encodeURIComponent(campaign.analysisSessionId)}`)
            .then((session) => {
                if (observedGraph.isConnected) observedGraph.replaceWith(renderObservedGraphDisclosure(session));
            })
            .catch((error) => {
                if (observedGraph.isConnected) observedGraph.textContent = `Recorded call graph unavailable: ${error.message}`;
            });
        loadManifestGate(campaign, manifestGate, runButton, runStatus).then(() => loadBaselineReadiness(
            campaign,
            readiness,
            runButton,
            runStatus,
            interventionConsole,
            frontendUrl
        ));
        if (dataSourceBadge) {
            dataSourceBadge.textContent = "EXPERIMENT · BASELINE REQUIRED";
            dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
        }
    }

    function renderVerifiedManifest(manifest, target, runButton, runStatus) {
        target.classList.add("manifest-gate--verified");
        target.replaceChildren();
        const identity = document.createElement("div");
        identity.append(
            guidedText("span", "guided-result__label", "Reproducibility record"),
            guidedText("h5", "", "Test settings saved"),
            guidedText("p", "", "You can now run the workflow normally.")
        );
        const badge = guidedText("span", "manifest-gate__badge", "VERIFIED");
        const details = document.createElement("div");
        details.className = "manifest-gate__details";
        details.append(
            resultCard("Algorithm", manifest.algorithmVersion.split("/").at(-1), "Recorded with the artifact"),
            resultCard("Random seed", String(manifest.randomSeed), "Repeatable degradation"),
            resultCard("Integrity", manifest.contentDigest.slice(0, 22) + "…", "SHA-256 protected")
        );
        const exportButton = guidedText("button", "secondary-button", "Download experiment record JSON");
        exportButton.type = "button";
        exportButton.addEventListener("click", () => {
            downloadJsonArtifact(`${manifest.workflowId}-experiment-manifest.json`, manifest);
        });
        const technical = document.createElement("details");
        technical.append(guidedText("summary", "", "Technical reproducibility details"),
            guidedText("p", "", `${manifest.manifestId} · ${manifest.conditionCount} planned telemetry-test conditions; this is not a count of executed tests.`), details, exportButton);
        target.append(identity, badge, technical);
        runButton.dataset.manifestVerified = String(manifest.integrityVerified);
        runButton.disabled = !manifest.integrityVerified;
        runStatus.textContent = manifest.integrityVerified
            ? "Manifest verified. Baseline collection is unlocked."
            : "Manifest integrity failed. Create a new campaign before collecting evidence.";
        runStatus.classList.toggle("is-error", !manifest.integrityVerified);
    }

    async function loadManifestGate(campaign, target, runButton, runStatus) {
        target.replaceChildren(
            guidedText("span", "guided-result__label", "Reproducibility gate"),
            guidedText("h5", "", "Freeze the experiment before collecting evidence"),
            guidedText("p", "", "Records the workflow contract, trace query, candidate services, thresholds, algorithm version, random seed, and missing-telemetry test matrix.")
        );
        try {
            const manifests = await requestScenario("/api/workflows/experiment-manifests");
            const existing = manifests.find((item) => item.campaignId === campaign.campaignId);
            if (existing) {
                renderVerifiedManifest(existing, target, runButton, runStatus);
                return;
            }
            const actions = document.createElement("div");
            actions.className = "manifest-gate__actions";
            const freezeButton = guidedText("button", "experiment-button", "Save test settings");
            freezeButton.type = "button";
            const help = guidedText("small", "", "Uses a recorded seed and five telemetry-completeness levels. Advanced values remain available in the downloaded JSON.");
            actions.append(freezeButton, help);
            target.appendChild(actions);
            freezeButton.addEventListener("click", async () => {
                freezeButton.disabled = true;
                freezeButton.textContent = "Freezing inputs…";
                try {
                    const manifest = await requestScenario(
                        `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/manifest`,
                        {
                            method: "POST",
                            headers: {
                                "Content-Type": "application/json",
                                "X-Actor": "guided-local-researcher",
                            },
                            body: JSON.stringify({
                                randomSeed: 1729,
                                degradationRates: [0, 0.05, 0.1, 0.2, 0.3],
                                repetitionsPerCondition: 5,
                            }),
                        }
                    );
                    renderVerifiedManifest(manifest, target, runButton, runStatus);
                } catch (error) {
                    freezeButton.disabled = false;
                    freezeButton.textContent = "Try freezing again";
                    runStatus.textContent = error.message;
                    runStatus.classList.add("is-error");
                }
            });
        } catch (error) {
            target.appendChild(guidedText("p", "baseline-console__status is-error", error.message));
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
            const progress = await requestScenario(
                `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/progress`
            );
            const readiness = progress.baseline;
            const root = target.closest('.guided-result') || guidedResult;
            const badge = root.querySelector('.guided-result__heading .guided-maturity');
            if (badge) badge.textContent = {baseline_required:'Step 2: baseline needed', interventions_required:'Step 3: dependency tests needed', ready_for_assessment:'Step 4: ready to review', reviewed:'Step 5: approval needed', approved:'Step 5: graph approved'}[progress.stage];
            const metric = root.querySelector('.guided-result__metrics .classification-result');
            if (metric) metric.replaceWith(resultCard(readiness.ready ? 'Baseline complete' : 'Baseline progress', `${readiness.eligibleTrials}/${readiness.requiredTrials}`, `${readiness.successfulTrials} successful · ${readiness.excludedTrials} excluded`));
            const baselineTitle = root.querySelector('.baseline-console__heading strong');
            if (baselineTitle) baselineTitle.textContent = readiness.ready ? 'Normal behavior confirmed' : `Collect ${readiness.requiredTrials} eligible normal executions`;
            const remaining = root.querySelector('.guided-next ol');
            if (remaining) remaining.replaceChildren(
                ...(
                    readiness.ready
                        ? ["Choose a service below to test, then record its result."]
                        : progress.blockers
                ).map(text => guidedText('li', '', text))
            );
            target.replaceChildren();
            const counts = document.createElement("div");
            counts.className = "baseline-readiness__counts";
            target.setAttribute("role", "status");
            target.setAttribute("aria-live", "polite");
            counts.append(
                resultCard("Eligible", `${readiness.eligibleTrials}/${readiness.requiredTrials}`, "Exactly correlated"),
                resultCard("Succeeded", String(readiness.successfulTrials), "Met the success contract"),
                resultCard("Failed", String(readiness.failedTrials), "Business outcome failed"),
                resultCard("Excluded", String(readiness.excludedTrials), "Not counted toward validation")
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
            if (readiness.excludedReasons?.length) {
                const excluded = document.createElement("ul");
                readiness.excludedReasons.forEach((reason) => excluded.appendChild(guidedText("li", "", `Excluded: ${reason}`)));
                gate.appendChild(excluded);
            }
            target.append(counts, gate);
            runButton.disabled = readiness.ready || runButton.dataset.manifestVerified !== 'true';
            if (readiness.ready) {
                runButton.textContent = "Baseline complete";
                runStatus.textContent = "The clean workflow baseline is stable and fully correlated.";
                updateExperimentPath(2);
                if (dataSourceBadge) dataSourceBadge.textContent = "EXPERIMENT · BASELINE READY";
                interventionConsole.hidden = false;
                await loadInterventionConsole(campaign, interventionConsole, frontendUrl);
                const baselineControls = runButton.closest(".baseline-console__controls");
                if (baselineControls) baselineControls.hidden = true;
            }
        } catch (error) {
            target.replaceChildren(guidedText("p", "baseline-console__status is-error", error.message));
            runButton.disabled = true;
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

    function isLocalBoutiqueDemo(campaign, applicationUrl) {
        return campaign.workloadProfile === "online_boutique_checkout_v0_10_1"
            && ["http://localhost:8080", "http://127.0.0.1:8080"].includes(applicationUrl.trim());
    }

    async function loadInterventionConsole(campaign, target, frontendUrl) {
        const applicationUrlLabel = frontendUrl.closest("label");
        target.replaceChildren();
        const heading = document.createElement("div");
        heading.className = "baseline-console__heading";
        heading.append(
            guidedText("div", "", "03  ·  CONTROLLED INTERVENTIONS"),
            guidedText("strong", "", "Test one downstream service at a time")
        );
        target.append(
            heading,
            applicationUrlLabel,
            guidedText(
                "p",
                "intervention-console__notice",
                isLocalBoutiqueDemo(campaign, frontendUrl.value)
                    ? "Use a local demo test below to stop one verified container, collect three trials, and restore it. Check the result before continuing."
                    : "For other environments, change one service only after approval; this page records operator evidence but cannot control the service."
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
                const badge = guidedResult.querySelector('.guided-result__heading .guided-maturity');
                if (badge) badge.textContent = 'Step 4: ready to review';
                const remaining = guidedResult.querySelector('.guided-next ol');
                if (remaining) remaining.replaceChildren(guidedText('li', '', 'Review the assessment, then explicitly approve before export.'));
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
        const card = document.createElement("details");
        card.className = `intervention-service intervention-service--${service.state}`;
        card.open = service.state === "active";
        const summary = document.createElement("summary");
        summary.className = "intervention-service__summary";
        const title = document.createElement("div");
        title.className = "intervention-service__title";
        title.append(
            guidedText("strong", "", service.targetService),
            guidedText("span", "guided-maturity", service.evidenceReady ? "evidence ready" : service.state)
        );
        const progress = guidedText(
            "p",
            "",
            `${service.eligibleTrials}/${service.requiredTrials} eligible workflow trials · ${service.excludedTrials} excluded`
        );
        const excludedDetails = document.createElement("details");
        if (service.excludedReasons?.length) {
            excludedDetails.appendChild(guidedText("summary", "", "Why trials were excluded"));
            const excludedList = document.createElement("ul");
            service.excludedReasons.forEach((reason) => excludedList.appendChild(guidedText("li", "", reason)));
            excludedDetails.appendChild(excludedList);
        }
        const controls = document.createElement("div");
        controls.className = "intervention-service__controls";
        const evidence = document.createElement("input");
        evidence.type = "text";
        evidence.placeholder = service.state === "active"
            ? "Restoration evidence (ticket, command result, observation)"
            : "Activation evidence (ticket, command result, observation)";
        const evidenceLabel = guidedText("label", "intervention-service__evidence-label", "Proof of service state");
        evidenceLabel.appendChild(evidence);
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
            const run = interventionActionButton("Run workflow trial", async () => {
                if (!frontendUrl.reportValidity()) return;
                run.disabled = true;
                message.textContent = "Executing the workflow under the confirmed intervention…";
                try {
                    await postCampaignAction(
                        `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/interventions/${encodeURIComponent(service.targetService)}/runs`,
                        {applicationUrl: frontendUrl.value.trim()}
                    );
                    await refresh();
                } catch (error) {
                    message.textContent = guidedConnectionError(error);
                    message.classList.add("is-error");
                    run.disabled = false;
                }
            });
            const restore = interventionActionButton("Record service restored", () => recordEvent("restored"));
            // Recovery must never depend on trial eligibility or completion.
            restore.disabled = false;
            controls.append(run, evidenceLabel, restore);
        } else {
            if (isLocalBoutiqueDemo(campaign, frontendUrl.value)) {
                const demoRun = interventionActionButton("Run local demo test · stop, test, restore", async () => {
                    if (!frontendUrl.reportValidity()) return;
                    if (!window.confirm(`Temporarily stop boutique-${service.targetService}, run its workflow trials, then restore it? Only use this on the local demo.`)) return;
                    demoRun.disabled = true;
                    message.textContent = `Testing ${service.targetService}; restoration will be attempted even if a trial fails…`;
                    message.classList.remove("is-error");
                    try {
                        await requestScenario(
                            `/api/workflows/experiment-campaigns/${encodeURIComponent(campaign.campaignId)}/local-demo/${encodeURIComponent(service.targetService)}/run-cohort`,
                            {
                                method: "POST",
                                headers: {
                                    "Content-Type": "application/json",
                                    "X-Actor": "guided-local-researcher",
                                    "X-Local-Demo-Confirm": "stop-and-restore-one-demo-service",
                                },
                                body: JSON.stringify({applicationUrl: frontendUrl.value.trim()}),
                            }
                        );
                        await refresh();
                    } catch (error) {
                        message.textContent = `${error.message} Check that boutique-${service.targetService} is running before retrying.`;
                        message.classList.add("is-error");
                        demoRun.disabled = false;
                    }
                });
                demoRun.classList.add("intervention-service__demo-action");
                controls.classList.add("intervention-service__controls--demo");
                controls.appendChild(demoRun);
            }
            const activate = interventionActionButton(
                service.state === "restored" ? "Record service unavailable again" : "Record service unavailable",
                () => recordEvent("activated")
            );
            controls.append(evidenceLabel, activate);
        }
        const instructions = document.createElement('details');
        instructions.append(guidedText('summary', '', 'Manual intervention and recovery instructions'),
            guidedText('p', '', 'These buttons only record operator attestation; they do not change or independently verify Docker/Kubernetes state. Obtain approval for this target and environment first. Stop only one dependency at a time. Record timestamped command output, run the trials, then restore and verify health. If testing fails, restore immediately outside this page; do not wait for the cohort to finish.'));
        const demoServices = ['cartservice', 'checkoutservice', 'currencyservice', 'emailservice', 'paymentservice', 'productcatalogservice', 'recommendationservice', 'shippingservice'];
        if (demoServices.includes(service.targetService)) {
            const name = `boutique-${service.targetService}`;
            instructions.append(guidedText('p', '', 'Local Online Boutique only: verify the container belongs to this demo before using these commands. Not instructions for production or Kubernetes.'),
                guidedText('pre', '', `docker inspect ${name} --format '{{.Config.Image}} {{.State.Status}}'\n# After approval, stop and verify:\ndocker stop ${name}\ndocker inspect ${name} --format '{{.State.Status}}'\n# Restore, including if the trial fails:\ndocker start ${name}\ndocker inspect ${name} --format '{{.State.Status}}'\ndocker logs --tail 20 ${name}`),
                guidedText('p', '', 'A running container alone does not prove recovery. Check its logs and a normal application request before confirming restoration. Retain the output as evidence.'));
        }
        summary.append(title, progress);
        card.appendChild(summary);
        if (service.excludedReasons?.length) card.appendChild(excludedDetails);
        card.append(instructions, controls, message);
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
        const groups = document.createElement("div");
        groups.className = "approved-graph";
        groups.append(
            resultCard("Mandatory", (assessment.mandatoryServices || []).join(", ") || "None", "Failure prevents the registered workflow outcome"),
            resultCard("Supportive", (assessment.supportingServices || []).join(", ") || "None", "Failure degrades the outcome without consistently preventing it"),
            resultCard("Optional", (assessment.optionalServices || []).join(", ") || "None", "No material outcome effect observed"),
            resultCard("Conditional", (assessment.conditionalServices || []).join(", ") || "None", "Classification differs across declared workflow conditions"),
            resultCard("Unresolved", assessment.services.filter((item) => item.classification === "insufficient_evidence").map((item) => item.serviceName).join(", ") || "None", "Not optional: more eligible evidence is required")
        );
        assessment.services.forEach((service) => {
            const row = document.createElement("article");
            row.append(
                guidedText("strong", "", service.serviceName),
                guidedText("span", "guided-maturity", service.classification.replaceAll("_", " ")),
                guidedText(
                    "small",
                    "",
                    `Baseline ${displaySuccessRate(service.baseline)} → intervention ${displaySuccessRate(service.intervention)} · evidence coverage ${Math.round(service.evidenceStrength * 100)}% (not statistical confidence)`
                ),
                guidedText("p", "", service.reasons.join(" "))
            );
            row.appendChild(conditionEvidenceDetails(service));
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
        const blocked = assessment.services.some((item) => ["conditional", "insufficient_evidence"].includes(item.classification));
        if (blocked) {
            approve.disabled = true;
            approvalStatus.textContent = "Approval blocked: resolve insufficient evidence or review condition-specific assessments. An unresolved dependency is not optional.";
        }
        target.append(groups, results, entry, rationale, confirmationLabel, approve, approvalStatus);
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
            resultCard("Mandatory", graph.mandatoryServices.join(", ") || "None", "Repeatedly prevented this tested checkout outcome"),
            resultCard("Supportive", (approval.supportingServices || approval.excludedSupportingServices || []).join(", ") || "None", "Recorded separately; excluded from Component 3 failure gating"),
            resultCard("Optional", graph.optionalServices.join(", ") || "None", "No material outcome effect observed"),
            resultCard("Observed call edges", String(graph.edges.length), "Trace-observed links between approved services; not individually outage-validated")
        );
        const exportButton = guidedText("button", "experiment-button", "Download validated graph JSON");
        exportButton.type = "button";
        exportButton.addEventListener("click", () => {
            downloadJsonArtifact(`${graph.workflowId}-validated-workflow-graph.json`, graph);
        });
        target.append(
            summary,
            guidedText("p", "assessment-console__entry", `Approved by ${approval.approvedBy}. ${approval.rationale}`),
            exportButton
        );
        updateExperimentPath(5);
        if (dataSourceBadge) dataSourceBadge.textContent = "EXPERIMENT · GRAPH APPROVED";
    }

    async function createGuidedCampaign(session, requiredTrials, campaignSetup, button, status) {
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
                    analysisSessionId: session.sessionId,
                    successContractConfirmed: true,
                    requiredBaselineTrials: requiredTrials,
                    ...campaignSetup,
                }),
            });
            renderCampaign(campaign);
                setGuidedStatus("Experiment planned. Freeze its reproducibility record before collecting baseline evidence.");
        } catch (error) {
            status.textContent = error.message;
            status.classList.add("is-error");
            button.disabled = false;
        }
    }

    function buildObservedWorkflowSetup(statusText, requestText, marker) {
        const codes = statusText.split(",").map((value) => value.trim());
        if (!codes.every((value) => /^[1-5]\d{2}$/.test(value))) {
            throw new Error("Enter comma-separated HTTP status codes between 100 and 599, for example 200, 201.");
        }
        const request = JSON.parse(requestText.trim() || "{}");
        if (!request || typeof request !== "object" || Array.isArray(request)) {
            throw new Error("Request options must be a JSON object.");
        }
        if (request.responseContains !== undefined && !Array.isArray(request.responseContains)) {
            throw new Error("responseContains must be a list of response markers.");
        }
        if (marker.trim()) request.responseContains = [...(request.responseContains || []), marker.trim()];
        return {mode: "observed_entry", expectedStatusCodes: [...new Set(codes.map(Number))], request};
    }

    function renderGuidedSession(session) {
        if (!guidedResult) return;
        guidedResult.hidden = false;
        guidedResult.replaceChildren();

        const heading = document.createElement("div");
        heading.className = "guided-result__heading";
        const identity = document.createElement("div");
        identity.append(
            guidedText("span", "guided-result__label", "Recorded workflow"),
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
            guidedText("strong", "", "Calls found"),
            guidedText(
                "span",
                "",
                "These services were called during the workflow. Testing is still needed to know which are required."
            )
        );

        const evidence = document.createElement("details");
        evidence.className = "guided-evidence";
        evidence.appendChild(guidedText("summary", "", "Service frequency in traces"));
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

        const contract = document.createElement("form");
        contract.className = "guided-contract";
        const observedEntrySupported = supportsObservedEntryProbe(session);
        contract.append(
            guidedText("span", "guided-result__label", "Success check"),
            guidedText(
                "h5",
                "",
                "What counts as success?"
            ),
            guidedText(
                "p",
                "",
                observedEntrySupported
                    ? "Name the result you expect and select the response to check. No workflow ID or graph is needed."
                    : "Import the HTTP steps needed to run this workflow and check its result."
            )
        );

        const workflowNameLabel = guidedText("label", "", "Workflow name");
        const workflowName = document.createElement("input");
        workflowName.required = true;
        workflowName.value = session.operationName;
        workflowNameLabel.appendChild(workflowName);

        const outcomeLabel = guidedText("label", "", "Successful result");
        const outcome = document.createElement("input");
        outcome.required = true;
        outcome.placeholder = "The customer-visible or system outcome that must succeed";
        outcomeLabel.appendChild(outcome);

        const statusesLabel = guidedText("label", "", "Which response status should the check accept?");
        const statuses = document.createElement("select");
        [["", "Choose the expected status"], ["200", "200 · OK"], ["201", "201 · Created"], ["204", "204 · No content"], ["custom", "Other status or multiple statuses"]].forEach(
            ([value, label]) => statuses.appendChild(new Option(label, value))
        );
        statuses.required = observedEntrySupported;
        statusesLabel.appendChild(statuses);
        const customStatusesLabel = guidedText("label", "", "Expected HTTP statuses");
        const customStatuses = document.createElement("input");
        customStatuses.placeholder = "200, 201";
        customStatuses.disabled = true;
        customStatusesLabel.hidden = true;
        customStatusesLabel.appendChild(customStatuses);
        statuses.addEventListener("change", () => {
            const custom = statuses.value === "custom";
            customStatusesLabel.hidden = !custom;
            customStatuses.disabled = !custom;
            customStatuses.required = custom && !definition.value.trim();
        });

        const responseLabel = guidedText("label", "", "Required response marker (optional)");
        const responseMarker = document.createElement("input");
        responseMarker.placeholder = "For example, a completion message from your application";
        responseLabel.appendChild(responseMarker);

        const requestDetails = document.createElement("details");
        requestDetails.className = "workflow-request-details";
        requestDetails.appendChild(guidedText("summary", "", "Request data · path variables, body, query and authentication"));
        const requestLabel = guidedText("label", "", "Request options (JSON object)");
        const requestOptions = document.createElement("textarea");
        requestOptions.rows = 7;
        requestOptions.value = "{}";
        requestOptions.spellcheck = false;
        requestOptions.placeholder = '{"pathVariables":{"id":"123"},"jsonBody":{"quantity":1},"secretHeaders":{"Authorization":"APP_AUTH_TOKEN"}}';
        requestLabel.appendChild(requestOptions);
        requestDetails.append(requestLabel, guidedText("small", "", "Supported: pathVariables, query, headers, secretHeaders, jsonBody, formBody, responseContains. Use server environment variable names in secretHeaders; never paste tokens. For routes such as /orders/{id}, supply pathVariables. Do not supply both JSON and form bodies."));

        const definitionLabel = guidedText("label", "", "Imported execution definition JSON (advanced)");
        const definition = document.createElement("textarea");
        definition.rows = 6;
        definition.placeholder = '{"runnerType":"http_sequence","steps":[...],"successCondition":{...}}';
        definitionLabel.appendChild(definition);
        const advancedDefinition = document.createElement("details");
        advancedDefinition.className = "workflow-request-details";
        advancedDefinition.open = !observedEntrySupported;
        advancedDefinition.append(guidedText("summary", "", "Advanced · imported multi-step execution"), definitionLabel);
        const effectiveChecks = guidedText('p', 'guided-contract__effective', 'Simple mode: the status, response marker and request options below define the check.');
        effectiveChecks.setAttribute('role', 'status');
        const syncDefinition = () => {
            const imported = Boolean(definition.value.trim());
            statuses.disabled = imported;
            responseMarker.disabled = imported;
            requestOptions.disabled = imported;
            customStatuses.disabled = imported || statuses.value !== 'custom';
            statuses.required = observedEntrySupported && !imported;
            customStatuses.required = statuses.value === "custom" && !imported;
            checkbox.checked = false;
            planButton.disabled = true;
            if (!imported) {
                effectiveChecks.textContent = 'Simple mode: status, response marker and request options define the check.';
                return;
            }
            try {
                const parsed = JSON.parse(definition.value);
                effectiveChecks.textContent = 'Imported sequence is active; simple fields are disabled. Effective checks: ' +
                    (parsed.steps || []).map(step => `${step.name}: ${step.method} ${step.path}; accepted status ${(step.expectedStatusCodes || []).join(', ')}${step.responseContains?.length ? '; response must contain ' + step.responseContains.join(', ') : ''}`).join(' → ') +
                    `. Measured step: ${parsed.measuredStep || 'not specified'}. Review before confirming.`;
            } catch {
                effectiveChecks.textContent = 'Invalid execution JSON. Correct it before confirming.';
            }
        };
        definition.addEventListener('input', syncDefinition);
        const templateButton = guidedText('button', 'secondary-button', 'Use Online Boutique checkout template');
        templateButton.type = 'button';
        templateButton.addEventListener('click', async () => {
            templateButton.disabled = true;
            try {
                const template = await requestScenario('/api/workflows/execution-templates/online-boutique-checkout');
                definition.value = JSON.stringify(template, null, 2);
                workflowName.value = 'Online Boutique checkout';
                outcome.value = 'Customer checks out a non-empty cart and receives the order-completion page.';
                syncDefinition();
            } catch (error) {
                effectiveChecks.textContent = `Template could not load: ${error.message}`;
            } finally { templateButton.disabled = false; }
        });
        if (session.operationName === 'POST /cart/checkout' && session.entryService === 'frontend') {
            contract.append(templateButton, guidedText('small', '', 'Optional local-demo template only. Loads product → cart → checkout with test data; it does not execute requests or prove a real payment settled. Review the checks below.'));
        }
        statusesLabel.hidden = !observedEntrySupported;
        responseLabel.hidden = !observedEntrySupported;
        requestDetails.hidden = !observedEntrySupported;
        contract.append(
            effectiveChecks,
            outcomeLabel,
            statusesLabel,
            customStatusesLabel,
            responseLabel,
            guidedText("small", "", "The description documents your intent. The status and response checks are what the runner measures. A successful HTTP response alone may not mean the business action completed; use a reviewed multi-step definition for a later result such as payment settlement."),
            requestDetails,
            advancedDefinition
        );

        const confirmation = document.createElement("label");
        confirmation.className = "guided-contract__confirmation";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        confirmation.append(
            checkbox,
            document.createTextNode(" I reviewed this execution definition and success contract for this application.")
        );
        const controls = document.createElement("div");
        controls.className = "guided-contract__controls";
        const trialsLabel = guidedText("label", "", "Baseline trials");
        const trials = document.createElement("input");
        trials.type = "number";
        trials.min = "3";
        trials.max = "30";
        trials.value = "5";
        trials.required = true;
        trialsLabel.appendChild(trials);
        const planButton = guidedText("button", "experiment-button", "Save workflow and continue");
        planButton.type = "submit";
        planButton.disabled = true;
        const planStatus = guidedText(
            "small",
            "guided-contract__status",
            "Saves the check. No service will be changed."
        );
        planStatus.setAttribute("role", "status");
        planStatus.setAttribute("tabindex", "-1");
        checkbox.addEventListener("change", () => {
            planButton.disabled = !checkbox.checked;
        });
        contract.addEventListener("submit", (event) => {
            event.preventDefault();
            if (!checkbox.checked || !contract.reportValidity()) return;
            planStatus.classList.remove("is-error");
            try {
                const imported = definition.value.trim();
                let executionSetup;
                if (imported) {
                    executionSetup = {executionDefinition: JSON.parse(imported)};
                } else {
                    if (!observedEntrySupported) {
                        throw new Error("Import an execution definition for this non-HTTP entry operation.");
                    }
                    executionSetup = {
                        workflowSetup: buildObservedWorkflowSetup(
                            statuses.value === "custom" ? customStatuses.value : statuses.value,
                            requestOptions.value,
                            responseMarker.value
                        ),
                    };
                }
                createGuidedCampaign(
                    session,
                    Number(trials.value),
                    {
                        workflowName: workflowName.value.trim(),
                        businessOutcome: outcome.value.trim(),
                        ...executionSetup,
                    },
                    planButton,
                    planStatus
                );
            } catch (error) {
                planStatus.textContent = error.message;
                planStatus.classList.add("is-error");
                planStatus.focus();
            }
        });
        const experimentOptions = document.createElement("details");
        experimentOptions.className = "workflow-request-details";
        experimentOptions.append(
            guidedText("summary", "", "Experiment options · display name and baseline trial count"),
            workflowNameLabel,
            trialsLabel,
            guidedText("small", "", "The name is filled from the selected operation. Baseline trial count is an experiment setting, not a graph-accuracy guarantee.")
        );
        controls.append(planButton, planStatus);
        contract.appendChild(experimentOptions);
        contract.append(confirmation, controls);
        contract.addEventListener("invalid", (event) => {
            for (let parent = event.target.parentElement; parent && parent !== contract; parent = parent.parentElement) {
                if (parent.tagName === "DETAILS") parent.open = true;
            }
        }, true);

        guidedResult.append(heading, metrics, warning, renderObservedGraph(session), evidence, contract);
        updateExperimentPath(0);
        if (dataSourceBadge) {
            dataSourceBadge.textContent = `${session.evidenceTier.toUpperCase()} · OBSERVED`;
            dataSourceBadge.className = "status-badge status-badge--muted status-badge--no-dot";
        }
    }

    async function discoverGuidedWorkflows(event) {
        if (event) event.preventDefault();
        if (!guidedForm || !guidedForm.reportValidity() || !guidedService?.value) return;
        const version = ++guidedConnectionVersion;
        guidedWorkflows = [];
        if (guidedResult) {
            guidedResult.hidden = true;
            guidedResult.replaceChildren();
        }
        const button = guidedForm.querySelector('[data-role="guided-discover-button"]');
        button.disabled = true;
        guidedInspectButton.disabled = true;
        guidedInspectButton.textContent = "Loading…";
        guidedOperation.replaceChildren();
        const loadingOption = document.createElement("option");
        loadingOption.value = "";
        loadingOption.textContent = "Loading recorded workflows…";
        guidedOperation.appendChild(loadingOption);
        setGuidedStatus("Loading available workflows…");
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
            if (version !== guidedConnectionVersion) return;
            guidedWorkflows = [...discovered].sort(
                (left, right) =>
                    Number(supportsGuidedExperiment(right)) -
                    Number(supportsGuidedExperiment(left))
            );
            guidedOperation.replaceChildren();
            guidedWorkflows.forEach((workflow, index) => {
                const option = document.createElement("option");
                option.value = String(index);
                option.textContent = `${workflow.operationName} · ${workflow.traceCount} traces`;
                guidedOperation.appendChild(option);
            });
            if (!guidedWorkflows.length) {
                const emptyOption = document.createElement("option");
                emptyOption.value = "";
                emptyOption.textContent = "No recorded workflows in this time window";
                guidedOperation.appendChild(emptyOption);
            }
            guidedDiscovery.hidden = false;
            updateGuidedSelectionAction();
            setGuidedStatus(
                guidedWorkflows.length
                    ? `Found ${guidedWorkflows.length} recorded operation${guidedWorkflows.length === 1 ? "" : "s"}. Choose the one you want to inspect.`
                    : "The trace service is connected, but no workflow activity was recorded in this time window.",
                guidedWorkflows.length === 0
            );
        } catch (error) {
            if (version !== guidedConnectionVersion) return;
            guidedWorkflows = [];
            guidedDiscovery.hidden = false;
            guidedOperation.replaceChildren();
            const unavailableOption = document.createElement("option");
            unavailableOption.value = "";
            unavailableOption.textContent = "No workflows available — check advanced settings";
            guidedOperation.appendChild(unavailableOption);
            updateGuidedSelectionAction();
            if (guidedResult) {
                guidedResult.hidden = true;
                guidedResult.replaceChildren();
            }
            setGuidedStatus(guidedConnectionError(error), true);
        } finally {
            if (version === guidedConnectionVersion) button.disabled = false;
        }
    }

    async function inspectGuidedWorkflow() {
        if (!guidedOperation || !guidedOperation.value) return;
        guidedInspectButton.disabled = true;
        const selected = selectedGuidedWorkflow(guidedWorkflows, guidedOperation.value);
        if (!selected) return;
        const version = ++guidedConnectionVersion;
        setGuidedStatus(
            supportsGuidedExperiment(selected)
                ? "Opening the workflow dependency experiment…"
                : "Preparing the observed-service summary…"
        );
        try {
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
            if (version !== guidedConnectionVersion) return;
            renderGuidedSession(session);
            setGuidedStatus("Observation recorded. No dependency-necessity claim has been made.");
        } catch (error) {
            if (version === guidedConnectionVersion) setGuidedStatus(error.message, true);
        } finally {
            if (version === guidedConnectionVersion) updateGuidedSelectionAction();
        }
    }

    async function loadLatestExperiment() {
        if (!guidedResult) return;
        try {
            const campaigns = await requestScenario("/api/workflows/experiment-campaigns");
            if (campaigns.length) {
                guidedResult.hidden = false;
                const label = guidedText('label', '', 'Saved workflow tests');
                const select = document.createElement('select');
                campaigns.forEach((campaign) => {
                    const date = campaign.createdAt
                        ? new Date(campaign.createdAt).toLocaleString(undefined, {dateStyle: 'medium', timeStyle: 'short'})
                        : campaign.environmentId;
                    select.appendChild(new Option(`${campaign.workflowName} · ${date}`, campaign.campaignId));
                });
                label.appendChild(select);
                const open = guidedText('button', 'experiment-button', 'Open run');
                open.type = 'button';
                const progress = guidedText('p', '', 'Choose a saved run to see its latest status.');
                open.addEventListener('click', () => {
                    renderCampaign(campaigns.find(c => c.campaignId === select.value));
                    setGuidedStatus('Saved run loaded. No test was started.');
                });
                guidedResult.replaceChildren(label, open, progress);
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
                scenarioChip(`${(scenario.conditions || []).length || 1} condition(s)`),
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
        const conditions = String(values.get("conditions") || "")
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean)
            .map((line) => {
                const separator = line.indexOf(":");
                if (separator < 1 || !line.slice(separator + 1).trim()) {
                    throw new Error("Each business condition must use 'condition-id: description'.");
                }
                return {
                    conditionId: line.slice(0, separator).trim(),
                    description: line.slice(separator + 1).trim(),
                };
            });
        return {
            workflowId: String(values.get("workflowId")).trim(),
            description: String(values.get("description")).trim(),
            criticality: Number(values.get("criticality")),
            entryService: String(values.get("entryService")).trim(),
            source: values.get("source"),
            lifecycle: values.get("lifecycle"),
            changeSummary: String(values.get("changeSummary")).trim(),
            conditions,
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

    function displaySuccessRate(cohort) {
        return cohort.successRate == null ? "not available" : `${Math.round(cohort.successRate * 100)}% (${cohort.eligibleRecords} eligible)`;
    }

    function conditionEvidenceDetails(service) {
        const details = document.createElement("details");
        details.className = "workflow-evidence-details";
        details.appendChild(guidedText("summary", "", "Inspect conditions and evidence provenance"));
        const conditions = service.conditionResults || [];
        (conditions.length ? conditions : [service]).forEach((item) => {
            details.append(
                guidedText("strong", "", `${item.conditionId || "default"} · ${item.classification.replaceAll("_", " ")}`),
                guidedText("p", "", `Baseline ${displaySuccessRate(item.baseline)}; intervention ${displaySuccessRate(item.intervention)}.`),
                guidedText("p", "", (item.reasons || []).join(" ")),
                guidedText("small", "", `Trace IDs: ${[...(item.baseline.eligibleTraceIds || []), ...(item.intervention.eligibleTraceIds || [])].join(", ") || "Not recorded in this assessment"}`),
                guidedText("small", "", `Excluded executions: ${(item.excludedExecutionIds || []).join(", ") || "None"}`)
            );
        });
        return details;
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
                (item) => ["insufficient_evidence", "conditional"].includes(item.classification)
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
            const groups = document.createElement("div");
            groups.className = "approved-graph";
            groups.append(
                resultCard("Mandatory", (assessment.mandatoryServices || []).join(", ") || "None", "Validated workflow blockers"),
                resultCard("Supportive", (assessment.supportingServices || []).join(", ") || "None", "Validated degradation dependencies"),
                resultCard("Optional", (assessment.optionalServices || []).join(", ") || "None", "No material success effect"),
                resultCard("Conditional", (assessment.conditionalServices || []).join(", ") || "None", "Review condition-specific results; global approval is blocked")
            );
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
                service.appendChild(conditionEvidenceDetails(item));
                services.appendChild(service);
            });
            record.append(heading, groups, services);
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
                jaegerStatusBadge.textContent = "Trace service connected";
                jaegerStatusBadge.className = "status-badge status-badge--success";
            } else {
                jaegerStatusBadge.textContent = "Trace service unavailable";
                jaegerStatusBadge.className = "status-badge status-badge--danger";
            }
        } catch (err) {
            jaegerStatusBadge.textContent = "Trace service unavailable";
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

            if (precisionEl) precisionEl.textContent = formatRatioAsPercentage(data.mandatoryServiceMetrics.precision);
            if (recallEl) recallEl.textContent = formatRatioAsPercentage(data.mandatoryServiceMetrics.recall);
            if (f1El) f1El.textContent = data.mandatoryServiceMetrics.f1.toFixed(2);
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
        const { assessmentId } = evaluationInputs();
        const btn = document.querySelector('[data-role="run-sensitivity-btn"]');
        if (!assessmentId) {
            alert("Enter a primary assessment ID in the Classification Validation Lab first.");
            return;
        }
        if (btn) {
            btn.disabled = true;
            btn.textContent = "Running…";
        }

        try {
            const thresholds = [0.5, 0.6, 0.7, 0.8, 0.9];
            const report = await requestScenario("/api/workflows/dependency-experiments/sensitivity", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    assessmentId,
                    cases: thresholds.map((threshold) => ({
                        caseId: `mandatory-effect-${threshold.toFixed(2)}`,
                        policy: {
                            mandatoryEffectThreshold: threshold,
                            optionalEffectTolerance: Math.min(0.1, threshold / 2),
                        },
                    })),
                }),
            });
            if (liveMode || version !== renderVersion) return;
            lastSensitivitySteps = report.cases;

            const container = document.querySelector('[data-role="sensitivity-table-container"]');
            const tbody = document.querySelector('[data-role="sensitivity-table-body"]');

            if (container && tbody) {
                tbody.innerHTML = "";
                report.cases.forEach((sensitivityCase) => {
                    const classifications = Object.values(sensitivityCase.classifications);
                    const mandatoryCount = classifications.filter((value) => value === "mandatory").length;
                    const optionalCount = classifications.filter((value) => value === "optional").length;
                    const tr = document.createElement("tr");
                    tr.innerHTML = `
                        <td><strong>${sensitivityCase.policy.mandatoryEffectThreshold.toFixed(2)}</strong></td>
                        <td colspan="4">${sensitivityCase.changedServices.join(", ") || "No label changes"}</td>
                        <td><span class="contract-chip">${mandatoryCount}</span></td>
                        <td><span class="contract-chip">${optionalCount}</span></td>
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
                btn.textContent = "Test Assessment Policies";
            }
        }
    }

    function handleCopyMarkdownTable() {
        if (liveMode || !lastSensitivitySteps.length) {
            alert("Run an assessment policy comparison before copying its results.");
            return;
        }
        let markdown = `### Frozen assessment policy sensitivity\n\n`;
        markdown += 'Classifications are recomputed from frozen cohort summaries; stored evidence is unchanged.\n\n';
        markdown += `| Mandatory Effect Threshold | Changed Services | Mandatory Count | Optional Count |\n`;
        markdown += `| :--- | :--- | :--- | :--- |\n`;

        if (lastSensitivitySteps.length > 0) {
            lastSensitivitySteps.forEach((sensitivityCase) => {
                const classifications = Object.values(sensitivityCase.classifications);
                const mandatoryCount = classifications.filter((value) => value === "mandatory").length;
                const optionalCount = classifications.filter((value) => value === "optional").length;
                markdown += `| **${sensitivityCase.policy.mandatoryEffectThreshold.toFixed(2)}** | ${sensitivityCase.changedServices.join(", ") || "None"} | ${mandatoryCount} | ${optionalCount} |\n`;
            });
        } else {
            return;
        }

        navigator.clipboard.writeText(markdown).then(() => {
            alert("Assessment policy sensitivity table copied.");
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
    if (guidedForm) {
        guidedForm.addEventListener("submit", connectGuidedSource);
        guidedForm.addEventListener("input", (event) => {
            if (event.target.name === "entryService") return;
            resetGuidedDiscovery();
            syncGuidedAuthentication();
            setGuidedStatus("Connection settings changed. Connect again to load services for these settings.");
        });
        guidedForm.addEventListener("invalid", (event) => {
            for (let parent = event.target.parentElement; parent && parent !== guidedForm; parent = parent.parentElement) {
                if (parent.tagName === "DETAILS") parent.open = true;
            }
        }, true);
        syncGuidedAuthentication();
    }
    if (guidedService) guidedService.addEventListener("change", () => {
        if (guidedService.value) {
            discoverGuidedWorkflows();
        } else {
            guidedConnectionVersion += 1;
            guidedWorkflows = [];
            guidedDiscovery.hidden = true;
            guidedResult.hidden = true;
            guidedResult.replaceChildren();
            guidedForm.querySelector('[data-role="guided-discover-button"]').disabled = false;
            updateGuidedSelectionAction();
            setGuidedStatus("Choose a recorded service to discover its workflows.");
        }
    });
    if (guidedInspectButton) guidedInspectButton.addEventListener("click", inspectGuidedWorkflow);
    if (guidedOperation) guidedOperation.addEventListener("change", () => {
        guidedConnectionVersion += 1;
        if (guidedResult) {
            guidedResult.hidden = true;
            guidedResult.replaceChildren();
        }
        updateGuidedSelectionAction();
    });
    if (telemetryReadinessForm) telemetryReadinessForm.addEventListener("submit", assessTelemetryReadiness);
    if (telemetrySourceType) {
        telemetrySourceType.addEventListener("change", () => {
            const sourceType = telemetrySourceType.value;
            const liveSource = ["jaeger", "zipkin", "tempo"].includes(sourceType);
            ["endpoint", "authentication", "secretEnv"].forEach((name) => {
                const field = telemetryReadinessForm.elements.namedItem(name);
                field.disabled = !liveSource;
                field.closest(".guided-field").hidden = !liveSource;
            });
            if (["jaeger", "zipkin", "tempo"].includes(sourceType) && telemetryEndpoint) {
                telemetryEndpoint.value = traceEndpointDefault(sourceType);
                syncGuidedTraceConnection(sourceType, telemetryEndpoint.value);
            }
        });
    }
    if (guidedTraceSource) {
        guidedTraceSource.addEventListener("change", () => {
            const endpointInput = guidedForm.elements.namedItem("traceEndpoint");
            const defaults = ["jaeger", "zipkin", "tempo"].map(traceEndpointDefault);
            if (endpointInput && defaults.includes(endpointInput.value)) {
                endpointInput.value = traceEndpointDefault(guidedTraceSource.value);
            }
        });
    }
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
        const sourcePill = document.querySelector('.topbar .pill-badge--teal');
        if (sourcePill) sourcePill.textContent = "No active campaign";
        setGuidedStatus("Connect your tracing backend to choose recorded services and workflows. No workflow executions run automatically.");
        document.querySelector('[data-role="resume-experiment"]')?.addEventListener("click", loadLatestExperiment);
        loadTelemetrySourceCapabilities();
    });
})();
