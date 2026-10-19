/*
 * Recommendation Evaluation and Validation page script.
 * Owned by: Member 4 (L.A. Nemal).
 *
 * Reads /api/recommendations/live: the recommendation report generated from the connected cluster's live
 * evidence, or an explicit error saying none exists. It never displays sample recommendations, placements
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

    const APPROVAL_BASE = "/api/recommendations";
    const PROPOSAL_VERSION = "v1.0";
    const approvalModal = document.getElementById("approval-modal");
    let currentRecommendation = null;

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    // ---- Approval gate -------------------------------------------------------------------------------

    async function callApproval(method, path, body) {
        const response = await fetch(`${APPROVAL_BASE}/${encodeURIComponent(currentRecommendation.id)}${path}`, {
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

        if (!approval) {
            stateContainer.append(approvalBadge("No Approval Requested", "muted"));
            const request = button("Request Approval", "primary-button", "btn-request-approval", requestApproval);
            if (!currentRecommendation.feasible) {
                request.disabled = true;
                request.title = "Recommendation must be feasible";
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
        if (action === "approve") {
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
        const result = await callApproval("POST", `/${action}`, {
            note,
            expectedProposalVersion: PROPOSAL_VERSION,
        });
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

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;
        region.replaceChildren();
        applyBadge(sourceBadge, { variant: "muted", text: "Loading…" });

        try {
            const report = await fetchJson("/api/recommendations/live");
            applyBadge(sourceBadge, { variant: "success", text: "Live evidence" });
            region.appendChild(template.content.cloneNode(true));

            // Evidence next to the report. Only existing producers are used; anything without one is null,
            // and the matching panel renders an explicit missing-evidence state.
            const context = {
                baselineBlastRadius: null,   // producer: a failure-impact result for the same topology (not wired here)
                validation: null,            // producer: none yet (post-deployment validation)
                yaml: null,                  // producer: the proposal stored with an approval request
                currentDeployment: null,     // producer: the topology the candidates were evaluated on (not exposed)
                simulatedDeployment: null,   // producer: the engine's virtual result (not exposed over HTTP)
            };

            currentRecommendation = view.selectedCandidate(report);
            if (currentRecommendation) {
                const approval = await loadApprovalState();
                context.yaml = (approval && approval.yamlSnapshot) || null;
            }

            showMetrics(view.summarise(report, context));
            view.renderCandidateCards(region.querySelector('[data-role="rec-candidate-list"]'), report, context);
            view.renderPlacement(
                region.querySelector('[data-role="rec-placement-current"]'),
                region.querySelector('[data-role="rec-placement-predicted"]'),
                currentRecommendation,
                context
            );
            view.renderRankingTable(region.querySelector('[data-role="rec-candidate-table-body"]'), report);
            view.renderValidation(region.querySelector('[data-role="rec-validation"]'), context.validation);
            view.renderYaml(region.querySelector('[data-role="rec-yaml"]'), context.yaml);
            initYamlToggle();
            initJsonPanel(region.querySelector("#rec-raw-recommendation"), report);
            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            applyBadge(sourceBadge, { variant: "muted", text: "No live recommendation" });
            renderStateCard(region, describeError(error), { onRetry: loadPage });
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
