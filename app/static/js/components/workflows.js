/*
 * Workflow Dependency Modeling page script.
 * Owned by: Member 1 (A.D. Perera).
 *
 * Only fetches /api/workflows/sample and formats it for display. Dependency
 * classification (mandatory/optional) and edge "type" labelling shown here
 * are UI presentation only, derived from fields the API already returns —
 * no new classification research logic is computed in this file.
 */

(function () {
    const ENDPOINT = "/api/workflows/sample";
    const { fetchJson } = window.ResilienceApi;
    const { setError, formatRatioAsPercentage, initJsonPanel, updateLastRefreshed } =
        window.ResilienceUi;

    const region = document.querySelector('[data-role="page-region"]');
    const template = document.querySelector('[data-role="page-template"]');
    const reloadButton = document.querySelector('[data-role="reload-button"]');
    const lastRefreshed = document.querySelector('[data-role="last-refreshed"]');

    function setMetric(root, name, value) {
        const element = root.querySelector(`[data-metric="${name}"]`);
        if (element) element.textContent = value;
    }

    function buildGraphNode(label, variant) {
        const node = document.createElement("div");
        node.className = `graph-flow__node graph-flow__node--${variant}`;
        node.textContent = label;
        return node;
    }

    function buildArrow() {
        const arrow = document.createElement("div");
        arrow.className = "graph-flow__arrow";
        arrow.textContent = "↓";
        arrow.setAttribute("aria-hidden", "true");
        return arrow;
    }

    function renderGraph(container, workflow) {
        container.innerHTML = "";
        container.className = "graph-flow";

        const mandatorySet = new Set(workflow.mandatoryServices);
        const entryService = workflow.edges.length
            ? workflow.edges[0].source
            : null;

        if (entryService && !mandatorySet.has(entryService)) {
            container.appendChild(buildGraphNode(entryService, "entry"));
            container.appendChild(buildArrow());
        }

        workflow.mandatoryServices.forEach((service, index) => {
            container.appendChild(buildGraphNode(service, "mandatory"));
            if (index < workflow.mandatoryServices.length - 1) {
                container.appendChild(buildArrow());
            }
        });

        if (workflow.optionalServices.length) {
            const branch = document.createElement("div");
            branch.className = "graph-flow__optional-branch";

            const label = document.createElement("span");
            label.className = "muted";
            label.textContent = "Optional:";
            branch.appendChild(label);

            workflow.optionalServices.forEach((service) => {
                branch.appendChild(buildGraphNode(service, "optional"));
            });

            container.appendChild(branch);
        }
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

    function edgeType(target, workflow) {
        if (workflow.mandatoryServices.includes(target)) {
            return "Mandatory path";
        }
        if (workflow.optionalServices.includes(target)) {
            return "Optional path";
        }
        return "Observed dependency";
    }

    function renderEdgeTable(tbody, workflow) {
        tbody.innerHTML = "";
        workflow.edges.forEach((edge) => {
            const row = document.createElement("tr");

            const sourceCell = document.createElement("td");
            sourceCell.textContent = edge.source;

            const targetCell = document.createElement("td");
            targetCell.textContent = edge.target;

            const typeCell = document.createElement("td");
            typeCell.textContent = edgeType(edge.target, workflow);

            row.append(sourceCell, targetCell, typeCell);
            tbody.appendChild(row);
        });
    }

    async function loadPage() {
        if (reloadButton) reloadButton.disabled = true;

        try {
            const workflow = await fetchJson(ENDPOINT);

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

            updateLastRefreshed(lastRefreshed);
        } catch (error) {
            setError(region, {
                endpoint: ENDPOINT,
                message: error.message,
                onRetry: loadPage,
            });
            console.error("Failed to load workflow sample data:", error);
        } finally {
            if (reloadButton) reloadButton.disabled = false;
        }
    }

    if (reloadButton) {
        reloadButton.addEventListener("click", loadPage);
    }

    document.addEventListener("DOMContentLoaded", loadPage);
})();
