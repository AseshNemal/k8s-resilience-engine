/*
 * Recommendation Evaluation and Validation page script.
 * Owned by: Member 4 (L.A. Nemal).
 *
 * Works from the connected cluster: recommendation-context.js asks the backend which application and approved frozen
 * workflow apply (and what evidence is missing), uses the only one automatically and offers a dropdown when there are
 * several, then this page shows /api/recommendations/live/detail: the recommendation generated from the live
 * evidence, with the engine's own explanation of each candidate, or an explicit statement of what is missing. It never displays sample recommendations, placements
 * or validation results; all scoring and simulation happen in the Python engine. The display components
 * live in recommendation-render.js and render exactly the evidence passed to them. Evidence that has no
 * producer yet (baseline blast radius, validation results, simulated placement) is passed as null, and each
 * panel then shows an explicit missing-evidence state. See docs/RECOMMENDATION_LIVE_DATA.md.
 *
 * Approval gate: an approval is requested for, and recorded against, the live recommendation shown here.
 * The server refuses to approve a record that has no verified link to live evidence.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { formatCodeLabel, initJsonPanel, updateLastRefreshed } = window.ResilienceUi;
    const view = window.ResilienceRecommendations;
    const { describeError, renderStateCard, applyBadge } = window.ResilienceConnection;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const sourceBadge = document.querySelector('[data-role="source-badge"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');
    // What to recommend for is chosen by recommendation-context.js (automatically, or from these dropdowns).
    const namespaceSelect = document.querySelector('[data-role="rec-namespace-select"]');
    const workflowSelect = document.querySelector('[data-role="rec-workflow-select"]');
    const nodeSelect = document.querySelector('[data-role="rec-node-select"]');
    const clusterLine = document.querySelector('[data-role="rec-cluster"]');
    const evidenceList = document.querySelector('[data-role="rec-evidence"]');
    const contextModule = window.ResilienceRecommendationContext;
    let controller = null;

    const APPROVAL_BASE = "/api/recommendations";
    const PROPOSAL_VERSION = "v1.0";
    const approvalModal = document.getElementById("approval-modal");
    let currentRecommendation = null;
    let currentApproval = { ready: false, reason: "" };   // the server's verdict for the selected candidate
    // Declared workflows only: the server's offer of the separate research tier for the selected candidate. The production controls
    // above stay off for such a candidate; this is a different door, with its own routes and a named person's acknowledgement.
    let currentResearch = null;
    let lastResearchName = "";                            // remembered in memory only, to save retyping within this page
    let researchNameInput = null;
    let researchAckInput = null;
    const RESEARCH_PLACEHOLDER = "I understand this workflow is declared by me, not observed, and approve it for research validation only.";

    function isDeclared() {
        return !!controller && controller.selection().workflowKind === "declared";
    }

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    // ---- Approval gate -------------------------------------------------------------------------------

    // The inputs an approval request must send, because the server recomputes the recommendation from them. They are
    // the controller's current choices, so they always belong to the cluster the person is looking at.
    function inputQuery() {
        const choice = controller ? controller.selection() : { workflowId: "", namespace: "", failedNode: "" };
        const params = new URLSearchParams();
        [["workflow_id", choice.workflowId], ["failed_node", choice.failedNode], ["namespace", choice.namespace]]
            .forEach(([name, value]) => { if (value) params.set(name, value); });
        const text = params.toString();
        return text ? `?${text}` : "";
    }

    async function callApproval(method, path, body) {
        // Requesting an approval recomputes the recommendation on the server, so it needs the same inputs.
        const query = path === "/request-approval" ? inputQuery() : "";
        // A declared workflow's approvals live under /declared: the research tier's own routes, never the production ones.
        const base = isDeclared() ? `${APPROVAL_BASE}/declared` : APPROVAL_BASE;
        const response = await fetch(`${base}/${encodeURIComponent(currentRecommendation.id)}${path}${query}`, {
            method,
            headers: { Accept: "application/json", ...(body ? { "Content-Type": "application/json" } : {}) },
            body: body ? JSON.stringify(body) : undefined,
        });
        let payload = null;
        try {
            payload = await response.json();
        } catch (_error) {
            payload = null;
        }
        return { ok: response.ok, status: response.status, payload, provenance: response.headers.get("X-Evidence-Provenance") };
    }

    function errorText(result, fallback) {
        const detail = result.payload && result.payload.detail;
        // Coded evidence errors carry a sanitised message. A plain string detail is shown only for client
        // errors (for example "Approval already pending"); server errors get the fallback, never raw text.
        if (detail && typeof detail === "object") return detail.message || fallback;
        return typeof detail === "string" && result.status < 500 ? detail : fallback;
    }

    function approvalBadge(text, variant) {
        const badge = document.createElement("span");
        badge.className = `status-badge status-badge--${variant}`;
        badge.textContent = text;
        return badge;
    }

    function button(label, className, id, onClick) {
        const element = document.createElement("button");
        element.type = "button";
        element.className = className;
        element.id = id;
        element.textContent = label;
        element.addEventListener("click", onClick);
        return element;
    }

    function renderApprovalState(approval, provenance, message) {
        const stateContainer = region.querySelector('[data-role="approval-state-container"]');
        const actionsContainer = region.querySelector('[data-role="approval-actions"]');
        if (!stateContainer || !actionsContainer) return;
        stateContainer.replaceChildren();
        actionsContainer.replaceChildren();

        if (!approval && isDeclared()) {
            renderResearchRequest(stateContainer, actionsContainer);
        } else if (!approval) {
            stateContainer.append(approvalBadge("No Approval Requested", "muted"));
            const request = button("Request Approval", "primary-button", "btn-request-approval", requestApproval);
            if (!currentRecommendation.feasible || !currentApproval.ready) {
                request.disabled = true;
                request.title = currentApproval.reason || "Recommendation must be feasible";
            }
            actionsContainer.append(request);
        } else {
            const variant = { PENDING_APPROVAL: "warning", APPROVED: "success", REJECTED: "danger" }[approval.status] || "muted";
            const stack = document.createElement("div");
            stack.className = "stack-sm";
            const version = document.createElement("div");
            version.className = "text-sm text-muted";
            version.textContent = `Proposal Version: ${approval.proposalVersion}`;
            stack.append(approvalBadge(approval.status, variant), version);
            if (provenance === "research-declared") {
                const research = document.createElement("div");
                research.className = "approval-note";
                research.setAttribute("data-role", "research-note");
                research.textContent = "Research tier: this workflow was declared, not observed. A decision here applies nothing and counts "
                    + "for validation on a designated test cluster only; results from it are conditional on the declaration.";
                stack.append(research);
            }
            if (provenance === "unverified") {
                const warning = document.createElement("div");
                warning.className = "approval-note";
                warning.textContent =
                    "This request has no verifiable link to live cluster evidence. It can be rejected but not approved.";
                stack.append(warning);
            }
            if (approval.decisionNote) {
                const note = document.createElement("div");
                note.className = "approval-note";
                const label = document.createElement("strong");
                label.textContent = "Note: ";
                note.append(label, document.createTextNode(approval.decisionNote));
                stack.append(note);
            }
            stateContainer.append(stack);

            if (approval.status === "PENDING_APPROVAL") {
                const reject = button("Reject", "danger-button", "btn-show-reject", () => showApprovalModal("reject"));
                const approve = button("Approve", "success-button", "btn-show-approve", () => showApprovalModal("approve"));
                if (provenance === "unverified") approve.disabled = true;
                actionsContainer.append(reject, approve);
            }
        }

        if (message) {
            const problem = document.createElement("div");
            problem.className = "approval-note";
            problem.setAttribute("role", "alert");
            problem.textContent = message;
            stateContainer.append(problem);
        }
    }

    // The research tier's request form, for a declared workflow only. The server decides whether the candidate may be put forward at all
    // (currentResearch.available); this only collects the named person and the written acknowledgement it requires.
    function renderResearchRequest(stateContainer, actionsContainer) {
        stateContainer.append(approvalBadge("No research approval requested", "muted"));
        const offer = currentResearch;
        if (!offer || !offer.available) {
            const why = document.createElement("div");
            why.className = "approval-note";
            why.textContent = (offer && offer.reason) || "This candidate cannot be put forward.";
            stateContainer.append(why);
            const blocked = button("Request research approval", "primary-button", "btn-request-research", () => {});
            blocked.disabled = true;
            actionsContainer.append(blocked);
            return;
        }
        const form = document.createElement("div");
        form.className = "stack-sm";
        const scope = document.createElement("div");
        scope.className = "approval-note";
        scope.textContent = offer.scope;
        researchNameInput = document.createElement("input");
        researchNameInput.type = "text";
        researchNameInput.className = "form-input";
        researchNameInput.id = "research-ack-by";
        researchNameInput.placeholder = "Your name";
        researchNameInput.value = lastResearchName;
        researchAckInput = document.createElement("textarea");
        researchAckInput.className = "form-input";
        researchAckInput.id = "research-ack-text";
        researchAckInput.rows = 3;
        researchAckInput.placeholder = RESEARCH_PLACEHOLDER;
        researchAckInput.value = "";
        form.append(scope, researchNameInput, researchAckInput);
        stateContainer.append(form);
        actionsContainer.append(button("Request research approval", "primary-button", "btn-request-research", requestResearchApproval));
    }

    async function requestResearchApproval() {
        const by = ((researchNameInput && researchNameInput.value) || "").trim();
        const statement = ((researchAckInput && researchAckInput.value) || "").trim();
        if (!by) return renderApprovalState(null, null, "Enter your name: a research approval is made by a named person.");
        lastResearchName = by;
        const result = await callApproval("POST", "/request-approval", {
            recommendationId: currentRecommendation.id,
            proposalVersion: PROPOSAL_VERSION,
            acknowledgedBy: by,
            acknowledgement: statement,
        });
        if (!result.ok) {
            return renderApprovalState(null, null, errorText(result, "Could not request the research approval."));
        }
        renderApprovalState(result.payload, result.provenance);
    }

    async function loadApprovalState() {
        const result = await callApproval("GET", "/approval");
        if (result.status === 404) {
            renderApprovalState(null);
            return null;
        }
        if (!result.ok) {
            renderApprovalState(null, null, errorText(result, "Could not load the approval state."));
            return null;
        }
        renderApprovalState(result.payload, result.provenance);
        return result.payload;
    }

    async function requestApproval() {
        const result = await callApproval("POST", "/request-approval", {
            recommendationId: currentRecommendation.id,
            proposalVersion: PROPOSAL_VERSION,
        });
        if (!result.ok) {
            return renderApprovalState(null, null, errorText(result, "Could not request approval."));
        }
        renderApprovalState(result.payload, result.provenance);
    }

    function showApprovalModal(action) {
        if (!approvalModal || !currentRecommendation) return;
        approvalModal.querySelector("#modal-context-type").textContent = formatCodeLabel(currentRecommendation.type);
        approvalModal.querySelector("#modal-context-service").textContent = currentRecommendation.service;
        const message = approvalModal.querySelector("#modal-message");
        const approve = approvalModal.querySelector("#modal-confirm-approve");
        const reject = approvalModal.querySelector("#modal-confirm-reject");
        approve.style.display = "none";
        reject.style.display = "none";
        // A research decision is made by a named person; the field is in the template and only shown for a declared workflow.
        const declared = isDeclared();
        const deciderGroup = approvalModal.querySelector("#research-decider-group");
        const decider = approvalModal.querySelector("#research-decider");
        if (deciderGroup) deciderGroup.hidden = !declared;
        if (decider) decider.value = declared ? lastResearchName : "";
        if (action === "approve" && declared) {
            message.textContent =
                "Approve this recommendation for RESEARCH validation? The workflow was declared, not observed, so anything measured from it is " +
                "conditional on that declaration. This approval does NOT apply the change; it only authorizes this exact proposal.";
            approve.style.display = "inline-flex";
            approve.onclick = () => submitApprovalDecision("approve");
        } else if (action === "approve") {
            message.textContent =
                "Approve this recommendation? This approval does NOT apply the change automatically. " +
                "It only authorizes this exact proposal for a future controlled test-environment application.";
            approve.style.display = "inline-flex";
            approve.onclick = () => submitApprovalDecision("approve");
        } else {
            message.textContent = "Reject this recommendation proposal?";
            reject.style.display = "inline-flex";
            reject.onclick = () => submitApprovalDecision("reject");
        }
        approvalModal.querySelector("#approval-note").value = "";
        approvalModal.classList.add("is-visible");
        approvalModal.setAttribute("aria-hidden", "false");
    }

    function hideApprovalModal() {
        if (!approvalModal) return;
        approvalModal.classList.remove("is-visible");
        approvalModal.setAttribute("aria-hidden", "true");
    }

    async function submitApprovalDecision(action) {
        const note = approvalModal.querySelector("#approval-note").value;
        const body = { note, expectedProposalVersion: PROPOSAL_VERSION };
        if (isDeclared()) {
            const decider = approvalModal.querySelector("#research-decider");
            body.decidedBy = ((decider && decider.value) || "").trim();
            if (body.decidedBy) lastResearchName = body.decidedBy;
        }
        const result = await callApproval("POST", `/${action}`, body);
        hideApprovalModal();
        if (!result.ok) {
            return renderApprovalState(null, null, errorText(result, "The decision could not be recorded."));
        }
        renderApprovalState(result.payload, result.provenance);
    }

    if (approvalModal) {
        const cancel = approvalModal.querySelector("#modal-cancel");
        if (cancel) cancel.addEventListener("click", hideApprovalModal);
    }

    function initYamlToggle() {
        const panel = region.querySelector("#rec-raw-yaml");
        const toggle = panel && panel.querySelector('[data-role="json-toggle"]');
        if (!toggle) return;
        toggle.addEventListener("click", () => {
            const isOpen = panel.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(isOpen));
            const label = toggle.querySelector('[data-role="json-toggle-label"]');
            if (label) label.textContent = isOpen ? "Hide Generated YAML" : "View Generated YAML";
        });
    }

    function showMetrics(summary) {
        const percent = (value) => (value === null || value === undefined ? "—" : `${Number(value).toFixed(0)}%`);
        setMetric(region, "rec-baseline-blast-radius", percent(summary.baseline));
        setMetric(region, "rec-best-predicted-blast-radius", percent(summary.bestPredicted));
        setMetric(
            region,
            "rec-risk-reduction",
            summary.selectedRiskReduction === null ? "—" : `${summary.selectedRiskReduction} pp`
        );
        setMetric(region, "rec-feasible-count", String(summary.feasibleCount));
    }

    // ---- Cluster, selections and evidence ----------------------------------------------------------------------------

    function fillSelect(select, options, selected, placeholder) {
        if (!select) return;
        const items = [];
        if (placeholder) {
            const empty = document.createElement("option");
            empty.value = "";
            empty.textContent = placeholder;
            items.push(empty);
        }
        for (const option of options) {
            const node = document.createElement("option");
            node.value = option.value;
            node.textContent = option.label;
            if (option.disabled) node.disabled = true;
            items.push(node);
        }
        select.replaceChildren(...items);
        select.value = selected || "";
        select.disabled = options.length === 0 || (options.length === 1 && !placeholder);
    }

    function renderContext(v) {
        if (clusterLine) {
            clusterLine.textContent = v.cluster.identity
                ? `Connected cluster: ${v.cluster.identity}`
                : (v.evidence.length ? "Connected cluster" : "Looking for the connected cluster…");
        }
        fillSelect(namespaceSelect,
            v.namespaces.map((row) => ({ value: row.namespace, label: `${row.namespace} (${row.services.length} services)` })),
            v.namespace, v.namespaceChoice === "ambiguous" || !v.namespace ? "Choose an application…" : null);
        fillSelect(workflowSelect,
            v.workflows.map((w) => ({ value: w.value, label: w.label, disabled: !w.applicable })),
            v.workflowValue, v.workflowChoice === "choose" || !v.workflowId ? (v.workflows.length ? "Choose a workflow…" : "No approved frozen workflow") : null);
        fillSelect(nodeSelect, v.failedNodes.map((name) => ({ value: name, label: name })), v.failedNode, "Worst case (the node whose loss hurts most)");
        view.renderEvidenceChecklist(evidenceList, v.evidence);
    }

    // What the person needs when the recommendation cannot be shown: the missing evidence and the exact next action.
    function blockedDescriptor(v) {
        if (v.error) {
            const lines = [...v.error.lines];
            if (v.error.nextAction) lines.push(`Next: ${v.error.nextAction}`);
            return { state: "missing-evidence", title: "Required evidence is not available", message: v.error.message, code: v.error.code, lines, retryable: v.error.retryable };
        }
        const missing = v.evidence.filter((item) => item.state === "missing" || item.state === "incomplete");
        const first = missing.find((item) => item.id !== "failureImpact") || missing[0];
        return {
            state: "missing-evidence",
            title: "Required evidence is not available",
            message: first ? first.detail : "A recommendation cannot be generated yet.",
            code: null,
            lines: v.nextActions.map((action) => `Next: ${action}`),
            retryable: true,
        };
    }

    function showDetail(v) {
        const detail = v.detail;
        const report = detail.report;
        region.replaceChildren();
        region.appendChild(template.content.cloneNode(true));

        // Only a candidate the engine selected is put to approval. Without a selection nothing is approvable, even though
        // the first candidate is still shown below (its placement and why it was not selected).
        const shown = view.selectedCandidate(report);
        currentRecommendation = report.selectedRecommendationId ? shown : null;
        const selected = currentRecommendation && detail.candidates.find((c) => c.id === currentRecommendation.id);
        currentApproval = selected ? selected.approval : { ready: false, reason: "No candidate is selected." };
        currentResearch = selected && selected.researchApproval ? selected.researchApproval : null;

        // Evidence next to the report: what the engine returned, never anything computed here. Without a YAML generator
        // and without applied changes there is no YAML proposal and no validation: those panels say so.
        const context = {
            baselineBlastRadius: detail.evidence.blastRadiusBefore,
            validation: null,
            yaml: detail.yaml && detail.yaml.available && detail.yaml.applicable ? detail.yaml.text : null,   // the engine's own proposal text, only when it is for live evidence
            currentDeployment: detail.currentDeployment,
            simulatedDeployment: selected ? selected.simulatedDeployment : null,   // only a selected, feasible candidate has one
        };
        view.renderOutcome(region.querySelector('[data-role="rec-outcome"]'), detail.outcome);
        view.renderCandidateDetails(region.querySelector('[data-role="rec-detail-list"]'), detail);
        showMetrics(view.summarise(report, context));
        view.renderCandidateCards(region.querySelector('[data-role="rec-candidate-list"]'), report, context);
        view.renderPlacement(region.querySelector('[data-role="rec-placement-current"]'),
            region.querySelector('[data-role="rec-placement-predicted"]'), shown, context);
        view.renderRankingTable(region.querySelector('[data-role="rec-candidate-table-body"]'), report);
        view.renderValidation(region.querySelector('[data-role="rec-validation"]'), context.validation);
        view.renderYaml(region.querySelector('[data-role="rec-yaml"]'), context.yaml);
        initYamlToggle();
        initJsonPanel(region.querySelector("#rec-raw-recommendation"), detail);
        updateLastRefreshed(lastRefreshed);

        if (currentRecommendation) {
            const requestedFor = currentRecommendation.id;
            loadApprovalState().then((approval) => {
                if (!currentRecommendation || currentRecommendation.id !== requestedFor) return;   // a newer view replaced it
                const yamlPanel = region.querySelector('[data-role="rec-yaml"]');
                if (yamlPanel && approval && approval.yamlSnapshot) view.renderYaml(yamlPanel, approval.yamlSnapshot);
            });
        } else {
            const container = region.querySelector('[data-role="approval-state-container"]');
            const actions = region.querySelector('[data-role="approval-actions"]');
            if (container) container.replaceChildren(approvalBadge("Nothing to approve", "muted"));
            if (actions) actions.replaceChildren();
        }
    }

    function render(v) {
        renderContext(v);
        const busy = v.status === "loading" || v.status === "loading-recommendation";
        if (reloadButton) reloadButton.disabled = busy;
        if (v.status === "ready") {
            const declared = v.workflowKind === "declared";
            applyBadge(sourceBadge, declared ? { variant: "warning", text: "Declared workflow · not approvable" }
                                             : { variant: "success", text: "Live evidence" });
            showDetail(v);
            if (declared) {
                // Standing label: this is not observed and cannot be approved, whatever the candidates below say.
                const note = document.createElement("p");
                note.className = "ui-note";
                note.setAttribute("data-role", "declared-note");
                note.textContent = (v.detail && v.detail.declared && v.detail.declared.notApprovableBecause)
                    || "This recommendation uses a workflow a person declared. It can be read, but it cannot be approved or applied.";
                region.prepend(note);
            }
            return;
        }
        currentRecommendation = null;
        currentApproval = { ready: false, reason: "" };
        currentResearch = null;
        if (busy) {
            applyBadge(sourceBadge, { variant: "muted", text: "Loading…" });
            region.replaceChildren();
            const note = document.createElement("p");
            note.className = "ui-note";
            note.textContent = v.status === "loading" ? "Reading the connected cluster and the approved workflows…" : "Asking for the current live recommendation…";
            region.appendChild(note);
        } else if (v.status === "needs-input") {
            applyBadge(sourceBadge, { variant: "muted", text: "Choose to continue" });
            region.replaceChildren();
            const note = document.createElement("p");
            note.className = "ui-note";
            note.textContent = "More than one application or workflow is available, so none was chosen for you. Choose above to continue.";
            region.appendChild(note);
        } else {
            applyBadge(sourceBadge, { variant: "muted", text: "Evidence missing" });
            renderStateCard(region, blockedDescriptor(v), { onRetry: () => controller.reload() });
        }
    }

    // The connected cluster's name, when this page runs inside the Docker Desktop extension. A page opened another way
    // has none, and the cluster is then told apart by the fingerprint the backend reports.
    async function clusterIdentity() {
        try {
            const response = await fetch("/_extension/cluster");
            if (!response.ok) return "";
            const body = await response.json();
            return body && typeof body.context === "string" ? body.context : "";
        } catch (_error) {
            return "";
        }
    }

    controller = contextModule.createController({
        // Discovery and the failure-impact prediction run server-side, so the default 8 s is too short.
        fetchJson: (url) => fetchJson(url, { timeout: 45000 }),
        clusterIdentity,
        onUpdate: render,
    });

    if (reloadButton) reloadButton.addEventListener("click", () => controller.reload());
    if (namespaceSelect) namespaceSelect.addEventListener("change", () => controller.selectNamespace(namespaceSelect.value));
    if (workflowSelect) workflowSelect.addEventListener("change", () => controller.selectWorkflow(workflowSelect.value));
    if (nodeSelect) nodeSelect.addEventListener("change", () => controller.selectFailedNode(nodeSelect.value));
    // Choices belong to one cluster: when the connection changes, nothing chosen before is kept.
    window.addEventListener("resilience-cluster-changed", () => controller.clusterChanged());

    document.addEventListener("DOMContentLoaded", () => controller.start());
})();
