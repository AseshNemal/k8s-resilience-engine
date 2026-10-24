/*
 * Recommendation Evaluation and Validation page script.
 * Owned by: Member 4 (L.A. Nemal).
 *
 * Works from the connected cluster: recommendation-context.js finds the application and available workflows,
 * then asks /api/recommendations/live/detail (or /declared/detail) with all_workflows=true for the recommendation generated from the live
 * evidence, with the engine's own explanation of each candidate, or an explicit statement of what is missing. It never displays sample recommendations, placements
 * or validation results; all scoring and simulation happen in the Python engine. The display components
 * live in recommendation-render.js and render exactly the evidence passed to them. Evidence that has no
 * producer yet (baseline blast radius, validation results, simulated placement) is passed as null, and each
 * panel then shows an explicit missing-evidence state. See docs/RECOMMENDATION_LIVE_DATA.md.
 *
 * The current template keeps candidate selection, its explanation and approval together. Workflow analysis stays behind the scenes,
 * and a combination stays one proposal. The legacy single-workflow template's step controls remain optional compatibility hooks.
 * Approving applies nothing; Apply and Reset remain explicit actions. Aggregate changes are labelled predictions without experimental proof.
 *
 * Approval gate: an approval is requested for, and recorded against, the live recommendation shown here.
 * The server refuses to approve a record that has no verified link to live evidence.
 */

(function () {
    const { fetchJson } = window.ResilienceApi;
    const { formatCodeLabel, updateLastRefreshed } = window.ResilienceUi;
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
    const allWorkflows = !workflowSelect;
    const applyTestRoot = document.querySelector('[data-role="rec-apply-test"]');
    const testHistoryRoot = document.querySelector('[data-role="rec-test-history"]');
    let testHistoryPanel = null;
    let historyStamp = "";
    let resultsSidebar = null;
    let sidebarHistory = { history: [], historyLoaded: false, hasMore: false, error: null };
    let applyTestPanel = null;
    const nodeSelect = document.querySelector('[data-role="rec-node-select"]');
    const clusterLine = document.querySelector('[data-role="rec-cluster"]');
    const evidenceList = document.querySelector('[data-role="rec-evidence"]');
    const contextModule = window.ResilienceRecommendationContext;
    let controller = null;
    // Compatibility with the earlier single-workflow template. The current page evaluates all workflows directly;
    // selecting a candidate keeps that candidate (including a whole combination) in the same approval flow.
    const portfolioModule = window.ResilienceRecommendationPortfolio;
    const portfolioRoot = document.querySelector('[data-role="rec-portfolio"]');
    let portfolioPanel = null;
    let preferredChange = null;                               // {service, type} chosen in that comparison; shown first when its workflow is reviewed

    // ---- Simple / Research view, the steps and where the person is ---------------------------------------------------
    const flowRoot = document.querySelector('[data-role="rec-flow"]');
    const stepperList = document.querySelector('[data-role="rec-stepper"]');
    const viewButtons = ["simple", "research"].map((name) => [name, document.querySelector(`[data-role="rec-view-${name}"]`)]);
    const VIEW_KEY = "resilience.recommendations.view";
    // Facts the stepper is drawn from. Nothing here is a result: it is only what the page already knows.
    const stepFacts = { chosen: false, status: "loading", selected: false, applied: false, proof: "none", proven: null, approval: "none" };
    let appliedFor = null;                                    // the recommendation the stored application belongs to, as the panel last reported
    let storedApplication = null;                             // the application the server holds, as the panel last reported
    let pinnedStep = null;                                    // a step the person opened; null while the page follows the work
    let shownStep = 1;
    const PAGE_STATE_KEY = "resilience.recommendations.page.v1";
    let restoredPage = false;
    let chosenTab = null;

    function pageScope(v) {
        return JSON.stringify([v.cluster.identity, v.cluster.fingerprint, v.namespace, v.workflowKind, v.workflowId, v.failedNode]);
    }

    function rememberPage() {
        if (!lastView || lastView.status !== "ready") return;
        try {
            window.localStorage.setItem(PAGE_STATE_KEY, JSON.stringify({
                scope: pageScope(lastView), step: pinnedStep || shownStep,
                tab: tabChosen ? chosenTab : null, preferredChange,
                scrollY: Math.max(0, window.scrollY || 0),
            }));
        } catch (_error) { /* The page still works when storage is blocked. */ }
    }

    function restorePage(v) {
        if (restoredPage || v.status !== "ready") return;
        restoredPage = true;
        try {
            const saved = JSON.parse(window.localStorage.getItem(PAGE_STATE_KEY) || "null");
            if (!saved || saved.scope !== pageScope(v)) return;
            if (Number.isInteger(saved.step) && saved.step >= 1 && saved.step <= 5) pinnedStep = saved.step;
            if (["failures", "ranking", "placement", "raw"].includes(saved.tab)) {
                tabChosen = true;
                selectTab(saved.tab, false);
            }
            const change = saved.preferredChange;
            if (change && typeof change.service === "string" && typeof change.type === "string") preferredChange = change;
            if (Number.isFinite(saved.scrollY) && saved.scrollY >= 0 && window.requestAnimationFrame && window.scrollTo) {
                window.requestAnimationFrame(() => window.scrollTo(0, saved.scrollY));
            }
        } catch (_error) { /* Ignore outdated or malformed page state. */ }
    }

    function storedView() {
        try {
            return window.localStorage.getItem(VIEW_KEY) === "research" ? "research" : "simple";
        } catch (_error) {
            return "simple";                                  // storage can be blocked: the page works without it
        }
    }

    function setView(mode, remember) {
        const chosen = mode === "research" ? "research" : "simple";
        if (flowRoot) flowRoot.setAttribute("data-view", chosen);
        viewButtons.forEach(([name, node]) => { if (node) node.setAttribute("aria-pressed", String(name === chosen)); });
        if (!remember) return;
        try {
            window.localStorage.setItem(VIEW_KEY, chosen);
        } catch (_error) {
            // the choice is simply not remembered
        }
    }

    /** The step the page opens at while the person has not chosen one: where the work is, not the last step that is open. */
    function stepAtWork() {
        if (!stepFacts.chosen) return 1;
        if (stepFacts.status !== "ready") return 2;           // loading shows its note, a missing piece of evidence shows what it is
        if (stepFacts.proof === "running" || stepFacts.proof === "attention") return 4;
        if (stepFacts.applied) return 5;
        return 2;
    }

    function updateStepper() {
        stepFacts.applied = !!currentRecommendation && appliedFor === currentRecommendation.id;
        const steps = view.computeSteps(stepFacts);
        const pin = pinnedStep && steps[pinnedStep - 1];
        // A pinned step stays while it can be opened; the applied-change panel can always be reached, even with no recommendation on screen.
        const open = pin && (pin.available || (pinnedStep === 5 && storedApplication));
        shownStep = open ? pinnedStep : stepAtWork();
        if (flowRoot) {
            flowRoot.setAttribute("data-step", String(shownStep));
            flowRoot.setAttribute("data-ready", String(!!currentRecommendation));
        }
        if (stepperList) view.renderStepper(stepperList, steps, { viewing: shownStep, onSelect: openStep });
        updateAround();
    }

    /** The person opens a step. It stays until the work they chose (the application, the workflow, the failure) changes. */
    function openStep(number) {
        pinnedStep = number;
        updateStepper();
        rememberPage();
        const heading = document.getElementById(`rec-step-${number}`);
        if (heading && heading.focus) {
            heading.setAttribute("tabindex", "-1");
            heading.focus({ preventScroll: true });
        }
        if (stepperList && stepperList.scrollIntoView) stepperList.scrollIntoView({ block: "start" });
    }

    function followTheWork() {
        pinnedStep = null;
    }

    // What is around the step on screen: the Next button of step 1, the applied-change notice, the research tab that fits the step.
    const appliedNotice = document.querySelector('[data-role="rec-applied-notice"]');
    const appliedText = document.querySelector('[data-role="rec-applied-text"]');
    const step1Next = document.querySelector('[data-role="rec-step1-next"]');
    const STEP_TAB = { 1: "failures", 2: "failures", 3: "ranking", 4: "placement", 5: "raw" };
    let tabChosen = false;                                    // once the person picks a tab, the page stops choosing one for them

    function updateAround() {
        if (step1Next) step1Next.hidden = !(stepFacts.status === "ready" && stepFacts.chosen);
        if (appliedNotice) {
            const hidden = !storedApplication || shownStep === 5;
            if (!hidden && appliedText) {
                appliedText.textContent = storedApplication.status === "RESET"
                    ? `Previous changes to ${storedApplication.namespace} are restored. Saved test reports remain available.`
                    : `A change is applied to ${storedApplication.namespace}: ${storedApplication.deployments.join(", ")} `
                        + `(${String(storedApplication.status).replace(/_/g, " ").toLowerCase()}).`;
            }
            appliedNotice.hidden = hidden;
        }
        if (!tabChosen) selectTab(STEP_TAB[shownStep], false);
    }

    // ---- Research tabs ---------------------------------------------------------------------------------------------
    const workbench = document.querySelector('[data-role="rec-workbench"]');

    function selectTab(name, focus) {
        chosenTab = name;
        if (!workbench || !workbench.querySelectorAll || !name) return;
        for (const tab of Array.from(workbench.querySelectorAll('[role="tab"]'))) {
            const on = tab.getAttribute("data-tab") === name;
            tab.setAttribute("aria-selected", String(on));
            tab.setAttribute("tabindex", on ? "0" : "-1");
            if (on && focus && tab.focus) tab.focus();
        }
        for (const pane of Array.from(workbench.querySelectorAll('[role="tabpanel"]'))) pane.hidden = pane.getAttribute("data-pane") !== name;
    }

    if (workbench && workbench.addEventListener) {
        workbench.addEventListener("click", (event) => {
            const tab = event.target && event.target.closest ? event.target.closest('[role="tab"]') : null;
            if (!tab) return;
            tabChosen = true;
            selectTab(tab.getAttribute("data-tab"), false);
            rememberPage();
        });
        workbench.addEventListener("keydown", (event) => {
            if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
            const tabs = Array.from(workbench.querySelectorAll('[role="tab"]'));
            const at = tabs.findIndex((tab) => tab.getAttribute("aria-selected") === "true");
            // Arrow keys move along the tabs and stop at the ends, as a tab list does.
            const next = tabs[Math.max(0, Math.min(tabs.length - 1, at + (event.key === "ArrowRight" ? 1 : -1)))];
            if (!next) return;
            tabChosen = true;
            selectTab(next.getAttribute("data-tab"), true);
            rememberPage();
            event.preventDefault();
        });
    }

    // Any button that says where it goes (data-go) opens that step: the footers, the case line and the notices.
    if (flowRoot && flowRoot.addEventListener) {
        flowRoot.addEventListener("click", (event) => {
            const target = event.target && event.target.closest ? event.target.closest("[data-go]") : null;
            const number = target ? Number(target.getAttribute("data-go")) : 0;
            if (number >= 1 && number <= 5) openStep(number);
        });
    }

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
    let lastApplierName = "";                             // remembered in memory only, to save retyping within this page
    const RESEARCH_PLACEHOLDER = "I understand this workflow is declared by me, not observed, and approve it for research validation only.";

    function isDeclared() {
        return !!controller && controller.selection().workflowKind === "declared";
    }

    // The research tabs sit outside the re-rendered region, so their metrics are looked up on the page.
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
        if (choice.allWorkflows) params.set("all_workflows", "true");
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

    // What a stored decision is called, in the words of its tier. The tiers are different doors, so their words differ: a research decision is
    // never worded as the production approval, and the production approval is never worded as research.
    const DECISION_WORDS = {
        production: { PENDING_APPROVAL: "Approval requested", APPROVED: "Approved", REJECTED: "Rejected" },
        research: { PENDING_APPROVAL: "Research approval requested", APPROVED: "Approved for research", REJECTED: "Research approval rejected" },
    };
    const TIER_LINE = {
        production: "Production approval, from live evidence.",
        research: "Research approval: the workflow was declared, not observed. It counts for validation on a test cluster only.",
    };

    function note(text, className) {
        const node = document.createElement("div");
        node.className = className || "approval-note";
        node.textContent = text;
        return node;
    }

    function shortDate(iso) {
        const date = iso ? new Date(iso) : null;
        return date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : null;
    }

    /** "Approved by Sam on 24 Oct 2026": only what the stored record says. */
    function decisionSummary(approval, words) {
        const decided = approval.status !== "PENDING_APPROVAL";
        const who = decided ? approval.decidedBy : approval.requestedBy;
        const when = shortDate(decided ? approval.decidedAt : approval.requestedAt);
        return `${words[approval.status] || approval.status}${who ? ` by ${who}` : ""}${when ? ` on ${when}` : ""}.`;
    }

    function renderApprovalState(approval, provenance, message) {
        if (applyTestPanel && allWorkflows) return renderApplyAction();
        const stateContainer = region.querySelector('[data-role="approval-state-container"]');
        const actionsContainer = region.querySelector('[data-role="approval-actions"]');
        if (!stateContainer || !actionsContainer) return;
        stateContainer.replaceChildren();
        actionsContainer.replaceChildren();
        stepFacts.approval = approval ? approval.status : "none";
        updateStepper();

        if (!approval && isDeclared()) {
            renderResearchRequest(stateContainer, actionsContainer);
        } else if (!approval) {
            stateContainer.append(approvalBadge("No approval requested", "muted"));
            const request = button("Request approval", "primary-button", "btn-request-approval", requestApproval);
            if (!currentRecommendation.feasible || !currentApproval.ready) {
                request.disabled = true;
                request.title = currentApproval.reason || "Recommendation must be feasible";
            }
            actionsContainer.append(request);
        } else {
            const research = provenance === "research-declared" || isDeclared();
            const words = DECISION_WORDS[research ? "research" : "production"];
            const variant = { PENDING_APPROVAL: "warning", APPROVED: "success", REJECTED: "danger" }[approval.status] || "muted";
            const stack = document.createElement("div");
            stack.className = "stack-sm";
            stack.setAttribute("data-role", "approval-record");
            stack.append(approvalBadge(words[approval.status] || approval.status, variant),
                note(decisionSummary(approval, words), "text-sm text-muted"));
            // A decision made before the change was proven is kept and shown, but it is an earlier approval and it is not a validation.
            const earlier = note(approval.status === "PENDING_APPROVAL"
                ? "This request was made without a test-cluster validation of this change."
                : "This decision was recorded without a test-cluster validation of this change, and it does not count as one.");
            earlier.className = "approval-note approval-history";
            earlier.hidden = stepFacts.proven !== false;      // shown once the server has said this change is not proven
            stack.append(earlier);
            const tier = note(research ? TIER_LINE.research : TIER_LINE.production, "text-sm text-muted");
            if (provenance === "research-declared") tier.setAttribute("data-role", "research-note");
            stack.append(tier);
            if (provenance === "unverified") {
                stack.append(note("This request has no verifiable link to live cluster evidence. It can be rejected but not approved."));
            }
            if (approval.decisionNote) {
                const noted = document.createElement("div");
                noted.className = "approval-note";
                const label = document.createElement("strong");
                label.textContent = "Note: ";
                noted.append(label, document.createTextNode(approval.decisionNote));
                stack.append(noted);
            }
            stateContainer.append(stack);
            if (approval.status === "APPROVED") renderApplication(stateContainer);

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

    function renderApplyAction() {
        const state = region.querySelector('[data-role="approval-state-container"]');
        const actions = region.querySelector('[data-role="approval-actions"]');
        if (!state || !actions) return;
        state.replaceChildren(note("Apply starts this selected change and its tests. No separate approval is required."));
        const selected = lastView?.detail?.candidates?.find((c) => c.id === currentRecommendation?.id);
        const eligible = isDeclared() ? selected?.researchApproval?.available : selected?.approval?.ready;
        const run = applyTestPanel.state();
        if (run.error || run.busy) {
            const feedback = note(run.error || (run.resetting ? "Restoring the previous configuration before another test. Your saved results are kept." : "Applying and testing. Follow the current operation in the run dashboard."));
            feedback.setAttribute("role", run.error ? "alert" : "status");
            state.append(feedback);
        }
        const apply = button(run.resetting ? "Restoring…" : run.busy ? "Running…" : run.error ? "Retry Apply" : "Apply", "primary-button", "btn-apply-and-test", async () => {
            const proposal = selected?.yamlProposal;
            if (!proposal?.fingerprint || !proposal?.sha256) return;
            let saved;
            try { saved = JSON.parse(window.sessionStorage.getItem("resilience.apply-test.request") || "null"); } catch (_e) { /* Optional storage. */ }
            if (!saved || saved.fingerprint !== proposal.fingerprint || saved.candidateId !== selected.id) {
                saved = { fingerprint: proposal.fingerprint, candidateId: selected.id,
                    key: window.crypto?.randomUUID?.() || `apply-${Date.now()}-${Math.random().toString(36).slice(2)}` };
                try { window.sessionStorage.setItem("resilience.apply-test.request", JSON.stringify(saved)); } catch (_e) { /* Optional storage. */ }
            }
            await applyTestPanel.apply({ candidateId: selected.id, namespace: lastView.namespace, declared: isDeclared(),
                fingerprint: proposal.fingerprint, yamlSha256: proposal.sha256, requestKey: saved.key });
        });
        apply.disabled = !eligible || !selected?.yamlProposal?.fingerprint || !selected?.yamlProposal?.sha256 || run.busy || !!run.run?.canReset;
        if (eligible && (!selected?.yamlProposal?.fingerprint || !selected?.yamlProposal?.sha256)) state.append(note("The selected change is missing its verified proposal. Refresh recommendations before Apply."));
        if (run.run?.canReset) state.append(note("The previous test is still applied. Reset it here to restore the baseline, then review and Apply the next candidate. All reports are kept."));
        if (!eligible) state.append(note(selected?.approval?.reason || "This candidate cannot be applied from the current evidence."));
        actions.replaceChildren(apply);
        if (run.run?.canReset && !run.busy) actions.append(button("Reset previous test", "secondary-button", "btn-reset-previous-test", () => applyTestPanel.reset()));
    }

    // The research tier's request form, for a declared workflow only. The server decides whether the candidate may be put forward at all
    // (currentResearch.available); this only collects the named person and the written acknowledgement it requires.
    function renderResearchRequest(stateContainer, actionsContainer) {
        stateContainer.append(approvalBadge("No research approval requested", "muted"));
        const offer = currentResearch;
        if (!offer || !offer.available) {
            const why = document.createElement("div");
            why.className = "approval-note";
            why.textContent = (offer && offer.reason) || "This candidate cannot be put forward for research approval.";
            stateContainer.append(why);
            const blocked = button("Request research approval", "primary-button", "btn-request-research", () => {});
            blocked.disabled = true;
            actionsContainer.append(blocked);
            return;
        }
        const form = document.createElement("div");
        form.className = "stack-sm";
        const scope = document.createElement("div");
        scope.className = "approval-note rec-scope";
        scope.textContent = offer.scope;
        scope.setAttribute("title", offer.scope);              // the plain view clamps it; the whole text stays one hover away
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
        const requestedFor = currentRecommendation.id;
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
        if (!currentRecommendation || currentRecommendation.id !== requestedFor) return;
        if (!result.ok) {
            return renderApprovalState(null, null, errorText(result, "Could not request the research approval."));
        }
        renderApprovalState(result.payload, result.provenance);
    }

    // ---- Apply (an approved change, on the connected cluster) ---------------------------------------------
    // The approval box only OFFERS Apply for the selected, approved recommendation. What happens after the click (the immediate "Applying
    // change…", the rollout, the analysis, Reset) belongs to the applied-change panel above, which is keyed by the application the server
    // stored and so outlives this box, the selection and the rankings (recommendation-operation.js).

    const operationModule = window.ResilienceRecommendationOperation;
    const operationRoot = document.querySelector('[data-role="rec-operation"]');
    const operationTemplate = document.querySelector('[data-role="operation-template"]');
    let applicationBox = null;                              // the Apply box currently on screen, and the recommendation it is for
    let applicationBoxFor = null;
    let applyConfirming = false;
    let operationPanel = null;

    function operationBusy() {
        return !!operationPanel && !!operationPanel.state().pending;
    }

    // Shown only for an APPROVED request. The server decides whether it can be applied (canApply) and re-checks everything when it is
    // asked; this collects the named person and asks once more before anything is written.
    function renderApplication(stateContainer) {
        const box = document.createElement("div");
        box.className = "stack-sm";
        box.setAttribute("data-role", "application");
        stateContainer.append(box);
        applicationBox = box;
        applicationBoxFor = currentRecommendation.id;
        applyConfirming = false;
        refreshApplication(box, applicationBoxFor);
    }

    async function refreshApplication(box, requestedFor) {
        const result = await callApproval("GET", "/application");
        if (!currentRecommendation || currentRecommendation.id !== requestedFor || applicationBox !== box) return;
        if (!result.ok) return fillApplication(box, null, errorText(result, "Could not read what has been applied."));
        fillApplication(box, result.payload);
    }

    function fillApplication(box, status, message) {
        box.replaceChildren();
        const heading = document.createElement("strong");
        heading.textContent = "Apply to the connected cluster";
        box.append(heading);
        if (!status) {
            if (message) box.append(applicationNote(message, true));
            return;
        }
        const application = status.application;
        const busy = operationBusy();
        if (application) {
            // Progress, the result and Reset are in the applied-change panel; this only says that it exists.
            const line = document.createElement("div");
            line.className = "text-sm text-muted";
            line.setAttribute("data-role", "application-detail");
            line.textContent = `${application.deployments.join(", ")} in ${application.namespace}: ${application.status.replace("_", " ").toLowerCase()}. `
                + "Its progress, result and Reset are in “Applied change” above.";
            box.append(line);
        }
        if (status.canApply) {
            const who = document.createElement("input");
            who.type = "text";
            who.className = "form-input";
            who.id = "apply-by";
            who.setAttribute("aria-label", "Your name");
            who.placeholder = "Your name";
            who.value = lastApplierName;
            who.disabled = busy;
            who.addEventListener("input", () => { lastApplierName = who.value; });
            box.append(who);
            const row = document.createElement("div");
            row.className = "approval-actions";
            if (applyConfirming) {
                box.append(applicationNote(operationModule ? operationModule.APPLY_NOTE : "Applying changes the connected cluster.", false));
                row.append(button("Confirm apply", "danger-button", "btn-confirm-apply", () => submitApplication(box, status, who)),
                    button("Cancel", "secondary-button", "btn-cancel-application", () => { applyConfirming = false; fillApplication(box, status); }));
            } else {
                row.append(button("Apply to cluster", "primary-button", "btn-apply", () => { applyConfirming = true; fillApplication(box, status); }));
            }
            Array.from(row.children || []).forEach((control) => { control.disabled = busy; });
            box.append(row);
        } else if (status.reason && !application) {
            box.append(applicationNote(status.reason, false));
        }
        if (message) box.append(applicationNote(message, true));
    }

    function applicationNote(text, alert) {
        const note = document.createElement("div");
        note.className = "approval-note";
        if (alert) note.setAttribute("role", "alert");
        note.textContent = text;
        return note;
    }

    async function submitApplication(box, status, whoInput) {
        const by = (whoInput.value || "").trim();
        if (!operationPanel || operationBusy()) return;                        // a second click while one request is in flight does nothing
        if (!by) return fillApplication(box, status, "Enter your name: applying a change is done by a named person.");
        lastApplierName = by;
        applyConfirming = false;
        const requestedFor = currentRecommendation.id;
        const started = operationPanel.startApply(requestedFor, isDeclared() ? "research" : "production", by);
        fillApplication(box, status);                                          // the controls are off the moment the request is on its way
        await started;
        if (applicationBox === box && currentRecommendation && currentRecommendation.id === requestedFor) refreshApplication(box, requestedFor);
    }

    // ---- "Prove it on a test cluster": its own panel, outside the re-rendered region, moved into the slot after every render ----------
    const proofModule = window.ResilienceRecommendationProof;
    const proofRoot = document.querySelector('[data-role="rec-proof"]');
    let proofPanel = null;
    let lastView = null;                                      // the controller's last view: what the proof step is for

    function proofContext() {
        const v = lastView || {};
        return { namespace: v.namespace || null, workflowId: v.workflowId || null, workflowSource: v.workflowKind === "declared" ? "DECLARED" : "FROZEN" };
    }

    /** Put the panel between the comparison and the approval, or take it out of the page when there is nothing to prove. */
    function placeProof() {
        if (!proofRoot) return;
        if (allWorkflows) { proofRoot.hidden = true; return; }
        const slot = region.querySelector('[data-role="rec-proof-slot"]');
        if (slot && currentRecommendation) {
            slot.appendChild(proofRoot);
            proofRoot.hidden = false;
        } else {
            proofRoot.hidden = true;
        }
    }

    function proofNoteText(proof) {
        if (!proof || proof.state === "NOT_RUN") return "Not proven on a test cluster.";
        const where = proof.target ? `${proof.target.name}` : "a test cluster";
        const depth = proof.preliminary ? "preliminary evidence" : "full validation";
        if (proof.state === "PROVEN") {
            return `Proven on ${where} (${depth}). That result stays with that cluster; applying to yours is checked again against it.`;
        }
        if (proof.state === "OTHER_PROVEN") return `A different change was proven on ${where}, not this one.`;
        return `Tested on ${where}; no change was proven.`;
    }

    /** The one place the answer to "was this change proven?" is written: the approval step's callout and the lead card's line. */
    function showProofStatus(proof, text, known) {
        const state = !known ? "unknown" : proof.state === "PROVEN" ? "proven" : proof.state === "OTHER_PROVEN" ? "other" : proof.state === "NOT_RUN" ? "unverified" : "failed";
        const note = region.querySelector('[data-role="rec-validation-note"]');
        const callout = region.querySelector('[data-role="rec-validation-callout"]');
        const go = region.querySelector('[data-role="rec-validation-go"]');
        const hero = region.querySelector(".candidate-card__validation-text");
        if (note && note.isConnected !== false) note.textContent = text;
        if (callout) callout.setAttribute("data-state", state);
        if (go) go.hidden = state === "proven";
        if (hero) hero.textContent = text;
        stepFacts.proven = known ? state === "proven" : null;
        const earlier = region.querySelector(".approval-history");
        if (earlier) earlier.hidden = stepFacts.proven !== false;
        updateStepper();
    }

    async function refreshProofNote() {
        const v = lastView || {};
        if (v.allWorkflows) {
            showProofStatus({ state: "NOT_RUN" }, applyTestPanel ? "Checks: rollout, configured workflow requests and measured resource usage. Node-failure risk is calculated from pod placement; no node-failure experiment runs here."
                : "Prediction only. This all-workflows change has not been proven by a fault-injection test.", true);
            if (applyTestPanel) {
                region.querySelector('[data-role="rec-validation-callout"]')?.setAttribute("data-state", "information");
                const go = region.querySelector('[data-role="rec-validation-go"]');
                if (go) go.hidden = true;
            }
            return;
        }
        if (!v.namespace || !v.workflowId) return;
        const sha = v.detail && v.detail.yaml ? v.detail.yaml.sha256 : null;
        const query = new URLSearchParams({ namespace: v.namespace, workflowId: v.workflowId });
        if (sha) query.set("candidateSha256", sha);
        try {
            const response = await fetch(`/api/recommendations/validation-loop/proof?${query}`);
            if (!response.ok) throw new Error("not available");
            const proof = await response.json();
            showProofStatus(proof, proofNoteText(proof), true);
        } catch (_error) {
            // Nothing is claimed that the server did not report: the status says it could not be read, and the step stays unproven.
            showProofStatus(null, "Whether this change was proven could not be read.", false);
        }
    }

    function proofChanged(fact) {
        stepFacts.proof = fact;
        updateStepper();
        if (fact !== "running") refreshProofNote();
    }

    function continueToApproval() {
        openStep(5);
    }

    // The panel tells the page when the stored application changed, so the Apply box never disagrees with it.
    function operationChanged(status) {
        appliedFor = status && status.application ? status.recommendationId : null;
        storedApplication = status && status.application ? status.application : null;
        updateStepper();
        if (applicationBox && status && status.recommendationId === applicationBoxFor && currentRecommendation
                && currentRecommendation.id === applicationBoxFor && applicationBox.isConnected !== false) {
            fillApplication(applicationBox, status);
        }
    }

    async function loadApprovalState() {
        const requestedFor = currentRecommendation.id;
        const result = await callApproval("GET", "/approval");
        if (!currentRecommendation || currentRecommendation.id !== requestedFor) return null;
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
        const requestedFor = currentRecommendation.id;
        const result = await callApproval("POST", "/request-approval", {
            recommendationId: currentRecommendation.id,
            proposalVersion: PROPOSAL_VERSION,
        });
        if (!currentRecommendation || currentRecommendation.id !== requestedFor) return;
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
                "conditional on that declaration. This approval does NOT apply the change; it only authorizes this exact proposal, and applying it is a separate step.";
            approve.style.display = "inline-flex";
            approve.onclick = () => submitApprovalDecision("approve");
        } else if (action === "approve") {
            message.textContent =
                "Approve this recommendation? This approval does NOT apply the change automatically. " +
                "It only authorizes this exact proposal; applying it to the cluster is a separate step.";
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

    function showMetrics(summary) {
        const percent = (value) => (value === null || value === undefined ? "—" : `${Number(value).toFixed(0)}%`);
        setMetric(document, "rec-baseline-blast-radius", percent(summary.baseline));
        setMetric(document, "rec-best-predicted-blast-radius", percent(summary.bestPredicted));
        setMetric(
            document,
            "rec-risk-reduction",
            summary.selectedRiskReduction === null ? "—" : `${summary.selectedRiskReduction} pp`
        );
        setMetric(document, "rec-feasible-count", String(summary.feasibleCount));
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

    // ---- every failure (each worker node as its own failure) --------------------------------------------

    const failuresButton = document.querySelector('[data-role="rec-failures-load"]');
    const failuresResult = document.querySelector('[data-role="rec-failures-result"]');
    let failuresGeneration = 0;
    let failuresFor = "";                                  // which workflow and application the table on screen was assessed for

    function failuresKey() {
        const choice = controller ? controller.selection() : { workflowId: "", namespace: "", workflowKind: "frozen" };
        return `${choice.workflowKind}|${choice.namespace}|${choice.workflowId}`;
    }

    async function loadFailures() {
        const choice = controller.selection();
        if (!failuresResult) return;
        if (!choice.workflowId) {
            return failuresResult.replaceChildren(approvalNoteElement("Choose a workflow first: the failures are assessed for one workflow.", true));
        }
        const params = new URLSearchParams({ workflow_id: choice.workflowId, namespace: choice.namespace });
        if (choice.allWorkflows) params.set("all_workflows", "true");
        const base = choice.workflowKind === "declared" ? "/api/recommendations/declared/failures" : "/api/recommendations/live/failures";
        const key = failuresKey();
        const generation = ++failuresGeneration;
        failuresFor = key;
        if (failuresButton) failuresButton.disabled = true;
        failuresResult.replaceChildren(approvalNoteElement("Assessing every worker node…", false));
        try {
            // One full evaluation per worker node runs on the server, so this is allowed longer than a single recommendation.
            const overview = await fetchJson(`${base}?${params.toString()}`, { timeout: 120000 });
            if (generation !== failuresGeneration || key !== failuresKey()) return;             // the person chose something else while it ran
            failuresFor = key;
            view.renderFailures(failuresResult, overview, (node) => {
                if (nodeSelect) nodeSelect.value = node;
                followTheWork();
                controller.selectFailedNode(node);
            });
        } catch (error) {
            if (generation !== failuresGeneration || key !== failuresKey()) return;
            failuresResult.replaceChildren(approvalNoteElement(
                (error && error.message) || "The failures could not be assessed.", true));
        } finally {
            if (failuresButton && generation === failuresGeneration) failuresButton.disabled = false;
        }
    }

    function approvalNoteElement(text, alert) {
        const note = document.createElement("div");
        note.className = "approval-note";
        if (alert) note.setAttribute("role", "alert");
        note.textContent = text;
        return note;
    }

    const PORTFOLIO_HINT = {
        frozen: "Pick a fix below, or choose one workflow above.",
        declared: "Pick a fix below, or choose one workflow above. Declared workflows are for research and are never picked for you.",
    };

    /** Which workflows the comparison across all of them covers, or null when it has nothing to add (fewer than two usable). */
    function portfolioSource(v) {
        return portfolioModule && portfolioPanel && v.namespace ? portfolioModule.pickSource(v.workflows) : null;
    }

    function updatePortfolio(v) {
        if (allWorkflows) return;
        if (!portfolioPanel) return;
        const source = portfolioSource(v);
        if (!source) return portfolioPanel.clear();
        if (v.status === "loading" && !v.workflows.length) return;     // nothing is known yet: keep what is on screen
        portfolioPanel.load(source, v.namespace, v.failedNode);
    }

    /** The person chose a fix in the comparison and a workflow to review it under: the ordinary review of that workflow takes over. */
    function reviewFromPortfolio(choice) {
        const option = ((lastView && lastView.workflows) || []).find((w) => w.workflowId === choice.workflowId && w.declared === (choice.source === "declared"));
        if (!option || !controller) return;
        preferredChange = { service: choice.service, type: choice.type };
        followTheWork();
        controller.selectWorkflow(option.value);
    }

    /**
     * The candidate the person chose in the comparison, when this workflow has it and it can be put forward; otherwise null and why.
     * Choosing never changes what the server ranked: the top-ranked change stays named, and approving another one needs the written
     * justification the server already requires.
     */
    function chosenFromComparison(detail, report) {
        if (!preferredChange) return { recommendation: null, note: null };
        const wording = `${view.strategyName(preferredChange.type)} on ${preferredChange.service}`;
        const wanted = detail.candidates.find((c) => preferredChange.id ? c.id === preferredChange.id
            : c.service === preferredChange.service && c.type === preferredChange.type);
        const recommendation = wanted && wanted.feasible ? report.recommendations.find((r) => r.id === wanted.id) || null : null;
        if (!recommendation) {
            return { recommendation: null, note: `The change you chose (${wording}) is not an option for this workflow, so this workflow's own top-ranked change is shown.` };
        }
        if (allWorkflows) return { recommendation, note: wanted.id === report.selectedRecommendationId
                ? "This is the highest-ranked fix across all workflows."
                : "You selected another ranked fix. Apply will run this change and its tests." };
        return {
            recommendation,
            note: wanted.id === report.selectedRecommendationId
                ? `You chose ${wording} from the all-workflows comparison. It is also the top-ranked change for this workflow.`
                : `You chose ${wording} from the all-workflows comparison. For this workflow another change ranks first, so approving this one needs a written justification.`,
        };
    }

    function renderContext(v) {
        // A table assessed for another workflow or application is not shown beside this one.
        if (failuresResult && (v.status === "loading" || (failuresFor && failuresFor !== failuresKey()))) {
            ++failuresGeneration;
            failuresResult.replaceChildren();
            failuresFor = "";
            if (failuresButton) failuresButton.disabled = false;
        }
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
        fillSelect(nodeSelect, v.failedNodes.map((name) => ({ value: name, label: name })), v.failedNode, "Worst case node");
        if (nodeSelect) nodeSelect.setAttribute("title", "The node whose loss hurts most is assessed unless you choose one.");
        // While the page waits for a choice, the field to use is marked and said in words.
        const hint = allWorkflows && v.namespace ? null : view.nextActionHint(v);
        [["namespace", namespaceSelect], ["workflow", workflowSelect]].forEach(([name, select]) => {
            if (!select) return;
            if (hint && hint.field === name) {
                select.setAttribute("data-attention", "true");
                select.setAttribute("aria-describedby", "rec-next-hint");
            } else {
                select.removeAttribute("data-attention");
                select.removeAttribute("aria-describedby");
            }
        });
        const hintLine = document.querySelector('[data-role="rec-hint"]');
        if (hintLine) {
            hintLine.textContent = hint ? (portfolioSource(v) && hint.field === "workflow" ? PORTFOLIO_HINT[portfolioSource(v)] : hint.text) : "";
            hintLine.hidden = !hint;
        }
        updatePortfolio(v);
        view.renderEvidenceChecklist(evidenceList, v.evidence);
        const summary = view.evidenceSummary(v.evidence);
        const evidenceSummaryLine = document.querySelector('[data-role="rec-evidence-summary"]');
        const evidenceDetails = document.querySelector('[data-role="rec-evidence-details"]');
        if (evidenceSummaryLine) evidenceSummaryLine.textContent = summary.text;
        // The list stays collapsed (the blocked card or the hint already says what to do); a mark on its line says it needs a look.
        if (evidenceDetails) {
            if (summary.attention) evidenceDetails.setAttribute("data-attention", "true");
            else evidenceDetails.removeAttribute("data-attention");
        }
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
            lines: first?.nextAction ? [`Next: ${first.nextAction}`] : v.nextActions.map((action) => `Next: ${action}`),
            retryable: true,
        };
    }

    // The research tabs sit outside the re-rendered region, so what fills them is looked up on the page, not in the region.
    const tabPart = (role) => document.querySelector(`[data-role="${role}"]`);

    /** Empty the research tabs, so nothing from an earlier recommendation is left beside a page that has none. */
    function clearWorkbench() {
        view.renderRankingTable(tabPart("rec-candidate-table-body"), { recommendations: [] });
        const reasons = tabPart("rec-detail-list");
        if (reasons) reasons.replaceChildren();
        view.renderPlacement(tabPart("rec-placement-current"), tabPart("rec-placement-predicted"), null, null);
        view.renderValidation(tabPart("rec-validation"), null);
        view.renderYaml(tabPart("rec-yaml"), null);
        const raw = tabPart("rec-raw-json");
        if (raw) raw.textContent = "Nothing loaded yet.";
        showMetrics({ baseline: null, bestPredicted: null, selectedRiskReduction: null, feasibleCount: 0 });
    }

    function showDetail(v) {
        const detail = v.detail;
        const report = detail.report;
        region.replaceChildren();
        region.appendChild(template.content.cloneNode(true));

        // Only a candidate the engine selected is put to approval. Without a selection nothing is approvable, even though
        // the first candidate is still shown below (its placement and why it was not selected).
        const chosen = chosenFromComparison(detail, report);
        const shown = chosen.recommendation || view.selectedCandidate(report);
        // Everything below that says "the selected change" must say the person's choice, so the report it reads names it as selected.
        const shownReport = chosen.recommendation ? { ...report, selectedRecommendationId: chosen.recommendation.id } : report;
        currentRecommendation = report.selectedRecommendationId ? shown : null;
        const selected = currentRecommendation && detail.candidates.find((c) => c.id === currentRecommendation.id);
        currentApproval = selected ? selected.approval : { ready: false, reason: "No candidate is selected." };
        currentResearch = selected && selected.researchApproval ? selected.researchApproval : null;
        stepFacts.approval = "none";                  // the stored decision of the new selection is read below
        stepFacts.proven = null;                      // and so is whether the server has it proven

        // Evidence next to the report: what the engine returned, never anything computed here. Without a YAML generator
        // and without applied changes there is no YAML proposal and no validation: those panels say so.
        const context = {
            baselineBlastRadius: detail.evidence.blastRadiusBefore,
            validation: null,
            yaml: chosen.recommendation
                ? (selected && selected.yamlProposal && selected.yamlProposal.status === "GENERATED" && selected.yamlProposal.applicable ? selected.yamlProposal.yaml : null)
                : (detail.yaml && detail.yaml.available && detail.yaml.applicable ? detail.yaml.text : null),   // the engine's own proposal text, only when it is for live evidence
            currentDeployment: detail.currentDeployment,
            simulatedDeployment: selected ? selected.simulatedDeployment : null,   // only a selected, feasible candidate has one
        };
        view.renderImpactSummary(region.querySelector('[data-role="rec-impact"]'), detail);
        const outcomeBox = region.querySelector('[data-role="rec-outcome"]');
        view.renderOutcome(outcomeBox, detail.outcome);
        if (chosen.note && outcomeBox && outcomeBox.append) {
            const note = document.createElement("p");
            note.className = "ui-note rec-chosen-note";
            note.textContent = chosen.note;
            outcomeBox.append(note);
        }
        view.renderProposalSummary(region.querySelector('[data-role="rec-proposal-summary"]'), detail,
            selected || null, v.cluster && v.cluster.identity ? v.cluster.identity : "");
        view.renderSelectedImprovement(region.querySelector('[data-role="rec-selected"]'), selected || null);
        // Each card says what it changes and, when it cannot be put forward, the engine's own reason.
        const descriptions = {};
        const reasons = {};
        detail.candidates.forEach((c) => {
            if (c.recommendedChange) descriptions[c.id] = c.recommendedChange;
            if (!c.feasible && c.approval.reason) reasons[c.id] = c.approval.reason;
        });
        // A declared workflow's cards do not repeat the notice that production approval is unavailable: it is said once, above the steps.
        view.renderCandidateCards(region.querySelector('[data-role="rec-candidate-list"]'), shownReport, context, {
            descriptions, reasons,
            ...(allWorkflows ? {
                dashboard: true,
                engineSelectedId: report.selectedRecommendationId,
                canSelect: (id) => {
                    const c = detail.candidates.find((item) => item.id === id);
                    return !!c && (isDeclared() ? !!c.researchApproval?.available : !!c.approval?.ready);
                },
                onSelect: selectCandidate,
            } : {}),
            hero: currentRecommendation ? { problem: view.problemStatement(detail),
                ...(allWorkflows ? {} : { validationText: "Checking whether it was proven on a test cluster…" }) } : null,
        });
        for (const role of ["rec-prove-action", "rec-review-action"]) {
            const action = region.querySelector(`[data-role="${role}"]`);
            if (action) action.hidden = allWorkflows || !currentRecommendation;
        }

        // The research tabs.
        view.renderPlacement(tabPart("rec-placement-current"), tabPart("rec-placement-predicted"), shown, context);
        // The whole topology now and after the selected change, from the same two graphs; only drawn when there is one to compare.
        const topologyBox = region.querySelector('[data-role="rec-topology-box"]');
        if (topologyBox && window.ResilienceTopologyCompare) {
            const model = window.ResilienceTopologyCompare.render(region.querySelector('[data-role="rec-topology"]'),
                context.currentDeployment, context.simulatedDeployment, { failedNode: (detail.evidence && detail.evidence.failedNode) || v.failedNode || null });
            topologyBox.hidden = !model;
        }
        view.renderRankingTable(tabPart("rec-candidate-table-body"), report);
        view.renderCandidateDetails(tabPart("rec-detail-list"), detail);
        view.renderValidation(tabPart("rec-validation"), context.validation);
        view.renderYaml(tabPart("rec-yaml"), context.yaml);
        const raw = tabPart("rec-raw-json");
        if (raw) raw.textContent = JSON.stringify(detail, null, 2);
        showMetrics(view.summarise(shownReport, context));
        updateLastRefreshed(lastRefreshed);
        // The engine's own statement about validation stays one hover away from the short line.
        const validationNote = region.querySelector('[data-role="rec-validation-note"]');
        if (validationNote && detail.validation && detail.validation.reason) validationNote.setAttribute("title", detail.validation.reason);

        stepFacts.selected = !!currentRecommendation;
        updateCandidateResults();
        updateStepper();                              // render() drew it before the selection was known
        placeProof();
        refreshProofNote();

        if (currentRecommendation) {
            if (applyTestPanel) { renderApplyAction(); return; }
            const requestedFor = currentRecommendation.id;
            loadApprovalState().then((approval) => {
                if (!currentRecommendation || currentRecommendation.id !== requestedFor) return;   // a newer view replaced it
                const yamlPanel = tabPart("rec-yaml");
                if (yamlPanel && approval && approval.yamlSnapshot) view.renderYaml(yamlPanel, approval.yamlSnapshot);
            });
        } else {
            const container = region.querySelector('[data-role="approval-state-container"]');
            const actions = region.querySelector('[data-role="approval-actions"]');
            if (container) container.replaceChildren(approvalBadge("Nothing to approve", "muted"));
            if (actions) actions.replaceChildren();
        }
    }

    // The one line that says what the steps are about, and, for a declared workflow, the one place that says what that means.
    const caseBox = document.querySelector('[data-role="rec-case"]');
    const caseLine = document.querySelector('[data-role="rec-case-line"]');
    const declaredNote = document.querySelector('[data-role="declared-note"]');

    function renderCase(v) {
        if (!caseBox || !caseLine) return;
        const chosen = !!(v.namespace && v.workflowId);
        caseBox.hidden = !chosen;
        if (!chosen) return;
        const failed = (v.detail && v.detail.evidence && v.detail.evidence.failedNode) || v.failedNode || "";
        const pairs = [["Application", v.namespace], ["Workflows", v.allWorkflows ? "All workflows" : v.workflowId], ["Failure", failed ? `${failed} stops` : "the worst-case node stops"]];
        caseLine.replaceChildren(...pairs.map(([label, value]) => {
            const item = document.createElement("span");
            item.className = "rec-case__item";
            const name = document.createElement("span");
            name.className = "rec-case__label";
            name.textContent = label;
            const text = document.createElement("span");
            text.className = "rec-case__value";
            text.textContent = value;
            item.append(name, text);
            return item;
        }));
    }

    function renderDeclaredNote(v, declared) {
        if (!declaredNote) return;
        declaredNote.hidden = !declared;
        if (!declared) return;
        // The engine's own statement, kept whole for the research view and as a tooltip; the plain view gets one sentence.
        const full = allWorkflows && applyTestPanel
            ? "The workflow definitions are declared rather than observed from traces. Apply starts the selected research change and its checks; results depend on those definitions. No separate approval form is required."
            : v.detail && v.detail.declared && v.detail.declared.notApprovableBecause;
        declaredNote.setAttribute("title", full || "");
        const short = document.createElement("span");
        short.textContent = allWorkflows ? "Declared workflows · research use only. Results depend on these definitions being correct."
            : "Declared workflow, not observed. It can be approved for research validation on a test cluster only; production approval is not available for it.";
        if (full) {
            const long = document.createElement("span");
            long.className = "rec-research-only";
            long.textContent = ` ${full}`;
            declaredNote.replaceChildren(short, long);
        } else {
            declaredNote.replaceChildren(short);
        }
    }

    function render(v) {
        lastView = v;
        restorePage(v);
        renderContext(v);
        renderCase(v);
        stepFacts.chosen = !!(v.namespace && v.workflowId);
        stepFacts.status = v.status;
        if (v.status !== "ready") stepFacts.selected = false;
        renderDeclaredNote(v, false);
        updateStepper();
        const busy = v.status === "loading" || v.status === "loading-recommendation";
        if (reloadButton) reloadButton.disabled = busy;
        if (v.status === "ready") {
            const declared = v.workflowKind === "declared";
            applyBadge(sourceBadge, declared ? { variant: "warning", text: applyTestPanel ? "Declared workflows · research only" : "Declared workflow · research approval only" }
                                             : { variant: "success", text: "Live evidence" });
            showDetail(v);
            renderDeclaredNote(v, declared);
            if (declared) {
                const outcome = region.querySelector('[data-role="rec-outcome"]');
                if (outcome) outcome.setAttribute("data-declared", "true");   // the notice above already says it; the plain view hides the repeat
            }
            return;
        }
        currentRecommendation = null;
        currentApproval = { ready: false, reason: "" };
        currentResearch = null;
        stepFacts.approval = "none";
        stepFacts.proven = null;
        if (proofRoot) proofRoot.hidden = true;
        clearWorkbench();
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
            if (!view.nextActionHint(v)) {        // with a hint the field to use is marked and the sentence is under it
                const note = document.createElement("p");
                note.className = "ui-note";
                note.textContent = "Choose an application and a workflow above to continue.";
                region.appendChild(note);
            }
        } else if (view.nextActionHint(v)) {
            // Only a declared workflow exists: nothing is broken, a choice is waiting, and the hint under step 1 says so.
            applyBadge(sourceBadge, { variant: "muted", text: "Choose to continue" });
            region.replaceChildren();
        } else {
            applyBadge(sourceBadge, { variant: "muted", text: "Evidence missing" });
            renderStateCard(region, blockedDescriptor(v), { onRetry: () => controller.reload() });
            // One sentence and the first thing to do; the rest of the list is for the research view.
            if (region.querySelectorAll) region.querySelectorAll(".error-state li").forEach((item, index) => { if (index > 0) item.classList.add("rec-research-only"); });
        }
        updateStepper();
        updateCandidateResults();
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

    if (operationModule && operationRoot && operationTemplate) {
        operationPanel = operationModule.createOperationPanel({
            root: operationRoot, template: operationTemplate, document,
            fetchImpl: (url, init) => fetch(url, init), onStatus: operationChanged,
        });
    }

    function downloadRunReport(report, filename) {
        const url = window.URL.createObjectURL(new Blob([JSON.stringify(report, null, 2)], { type: "application/json" }));
        const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename;
        anchor.click(); window.URL.revokeObjectURL(url);
    }

    // The page shows the data of the SELECTED candidate. The live run panel is shown only while the selected candidate is the one the
    // latest run belongs to; any other selection shows that candidate's latest saved run, or its prediction when it was never tested.
    const selectedDataRoot = document.querySelector('[data-role="rec-selected-data"]');
    let selectedViewer = null;
    let selectedViewerRun = null;

    function dropSelectedViewer() {
        if (selectedViewer) selectedViewer.destroy();
        selectedViewer = null;
        selectedViewerRun = null;
    }

    function updateSelectedData() {
        if (!selectedDataRoot || !applyTestRoot || !applyTestPanel) return;
        const part = (role) => selectedDataRoot.querySelector(`[data-role="${role}"]`);
        const chosen = lastView?.status === "ready" ? currentRecommendation?.id : null;
        const live = applyTestPanel.state().run;
        const showLive = !chosen || !!(live && live.candidateId === chosen);
        applyTestRoot.setAttribute("data-shows", showLive ? "live" : "other");
        selectedDataRoot.hidden = showLive;
        if (showLive) { dropSelectedViewer(); return; }
        const candidate = lastView.detail?.candidates?.find((item) => item.id === chosen);
        const body = part("rec-selected-data-body");
        part("rec-selected-data-title").textContent = candidate
            ? `${view.strategyName(candidate.type)}${candidate.service ? ` on ${candidate.service}` : ""}` : "Selected candidate data";
        const saved = (sidebarHistory.history || []).find((row) => row.candidateId === chosen && (!lastView.namespace || row.namespace === lastView.namespace));
        if (saved) {
            part("rec-selected-data-note").textContent = "Selected candidate: its latest saved test. Nothing is applied by selecting.";
            if (selectedViewerRun !== saved.runId) {
                dropSelectedViewer();
                selectedViewerRun = saved.runId;
                body.replaceChildren();
                selectedViewer = window.ResilienceApplyTest.createPanel({ root: body, document, fetchImpl: (url, init) => fetch(url, init),
                    runId: saved.runId, readOnly: true, download: downloadRunReport });
                selectedViewer.start();
            }
            return;
        }
        dropSelectedViewer();
        const predicted = candidate?.predicted;
        part("rec-selected-data-note").textContent = sidebarHistory.historyLoaded === false ? "Reading saved results…"
            : sidebarHistory.hasMore ? "No saved test among the results loaded so far."
            : "Not tested yet. Apply runs this change and its checks.";
        body.replaceChildren();
        if (predicted) {
            const line = document.createElement("p");
            line.textContent = `Predicted model risk: ${predicted.blastRadiusBefore}% now → ${predicted.blastRadiusAfter}% after the change. A prediction from the simulation, not a measurement.`;
            body.append(line);
        }
    }

    function updateCandidateResults() {
        updateSelectedData();
        const applied = applyTestPanel?.state();
        resultsSidebar?.update({ ...sidebarHistory, namespace: lastView?.namespace,
            activeId: applied?.active ? applied.run.candidateId : null, activeRun: applied?.active ? applied.run : null,
            selectedId: lastView?.status === "ready" ? currentRecommendation?.id : null,
            candidates: lastView?.status === "ready" ? lastView.detail?.candidates || [] : [] });
    }

    function selectCandidate(id) {
        const v = lastView;
        const candidate = v?.status === "ready" && v.detail?.candidates?.find((item) => item.id === id);
        if (!candidate || !(isDeclared() ? candidate.researchApproval?.available : candidate.approval?.ready)) return;
        preferredChange = { id: candidate.id, service: candidate.service, type: candidate.type };
        showDetail(v);
        rememberPage();                                       // selecting only changes what is shown; the sidebar follows and the page stays where it is
    }

    const resultsSidebarRoot = document.querySelector('[data-role="rec-results-sidebar"]');
    if (allWorkflows && resultsSidebarRoot && window.ResilienceApplyTest) {
        resultsSidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root: resultsSidebarRoot, document,
            strategyName: view.strategyName, onSelect: selectCandidate,
            onReport: (runId) => testHistoryPanel?.openRun(runId), onLoadOlder: () => testHistoryPanel?.loadOlder() });
    }

    if (allWorkflows && testHistoryRoot && window.ResilienceApplyTest) {
        testHistoryPanel = window.ResilienceApplyTest.createHistoryPanel({ root: testHistoryRoot, document,
            fetchImpl: (url, init) => fetch(url, init), storage: window.localStorage, download: downloadRunReport,
            onRows: (rows, meta) => { sidebarHistory = { history: rows, ...meta }; updateCandidateResults(); } });
    }

    if (allWorkflows && applyTestRoot && window.ResilienceApplyTest) {
        if (operationRoot) operationRoot.setAttribute("data-managed-run", "true");
        applyTestPanel = window.ResilienceApplyTest.createPanel({
            root: applyTestRoot, document, fetchImpl: (url, init) => fetch(url, init),
            onChange: () => {
                const run = applyTestPanel?.state().run;
                if (operationRoot) operationRoot.setAttribute("data-managed-run", String(!!run));
                if (currentRecommendation) renderApplyAction();
                updateCandidateResults();
                const stamp = run ? `${run.runId}:${run.status}` : "";
                if (stamp !== historyStamp) { historyStamp = stamp; testHistoryPanel?.refresh(); }
            },
            onReset: () => { if (operationPanel) operationPanel.start(); },
            onReadyForNext: () => {
                // Re-evaluate the person's choice after restoration; never start it automatically.
                controller.reload();
                testHistoryPanel?.refresh();
                operationPanel?.start();
            },
            pendingRequestKey: () => { try { return JSON.parse(window.sessionStorage.getItem("resilience.apply-test.request") || "null")?.key; } catch (_e) { return null; } },
            clearRequestKey: () => { try { window.sessionStorage.removeItem("resilience.apply-test.request"); } catch (_e) { /* Optional storage. */ } },
            download: downloadRunReport,
        });
    }

    if (proofModule && proofRoot && !allWorkflows) {
        proofPanel = proofModule.createProofPanel({
            root: proofRoot, document, fetchImpl: (url, init) => fetch(url, init), getContext: proofContext, onChange: proofChanged,
            isResearch: () => !!flowRoot && flowRoot.getAttribute("data-view") === "research", onContinue: continueToApproval,
        });
    }

    if (portfolioModule && portfolioRoot) {
        portfolioPanel = portfolioModule.createPortfolioPanel({
            root: portfolioRoot, document, strategyName: view.strategyName, onReview: reviewFromPortfolio,
            // One full evaluation per workflow and per node runs on the server, so this is allowed longer than a single recommendation.
            fetchJson: (url) => fetchJson(url, { timeout: 120000 }),
        });
    }

    controller = contextModule.createController({
        allWorkflows,
        // Aggregation evaluates every workflow and worker on the server.
        fetchJson: (url) => fetchJson(url, { timeout: allWorkflows ? 120000 : 45000 }),
        clusterIdentity,
        onClusterChange: () => {
            preferredChange = null;
            if (portfolioPanel) portfolioPanel.clear();
            followTheWork();
            if (operationPanel) operationPanel.clusterChanged();
            if (proofPanel) proofPanel.reset();
            if (applyTestPanel) applyTestPanel.clusterChanged();
        },
        storage: {
            getItem: (key) => window.localStorage.getItem(key),
            setItem: (key, value) => window.localStorage.setItem(key, value),
            removeItem: (key) => window.localStorage.removeItem(key),
        },
        onUpdate: render,
    });

    viewButtons.forEach(([name, node]) => { if (node) node.addEventListener("click", () => setView(name, true)); });
    setView(storedView(), false);
    updateStepper();

    // Changing what the page is about starts the steps over: the page follows the work again.
    if (reloadButton) reloadButton.addEventListener("click", () => { followTheWork(); controller.reload(); });
    // Choosing by hand ends a choice made in the comparison: the change is no longer carried over to whatever is picked next.
    if (namespaceSelect) namespaceSelect.addEventListener("change", () => { preferredChange = null; followTheWork(); controller.selectNamespace(namespaceSelect.value); });
    if (workflowSelect) workflowSelect.addEventListener("change", () => { preferredChange = null; followTheWork(); controller.selectWorkflow(workflowSelect.value); });
    if (nodeSelect) nodeSelect.addEventListener("change", () => { followTheWork(); controller.selectFailedNode(nodeSelect.value); });
    if (failuresButton) failuresButton.addEventListener("click", loadFailures);
    // Notifications include routine status checks. Only a verified cluster change clears this page.
    window.addEventListener("resilience-cluster-changed", () => controller.connectionChanged());
    window.addEventListener("pagehide", () => {
        rememberPage();
        if (operationPanel) operationPanel.destroy();
        if (proofPanel) proofPanel.destroy();
        if (applyTestPanel) applyTestPanel.destroy();
        dropSelectedViewer();
        if (testHistoryPanel) testHistoryPanel.destroy();
    });
    document.addEventListener("visibilitychange", () => { if (document.hidden) rememberPage(); });

    document.addEventListener("DOMContentLoaded", () => {
        controller.start();
        if (operationPanel) operationPanel.start();         // finds an applied change again after a reload; never applies anything
        if (proofPanel) proofPanel.start();                 // finds a test run again after a reload or a restart; never starts one
        if (applyTestPanel) applyTestPanel.start();
        if (testHistoryPanel) testHistoryPanel.start();
    });
})();
