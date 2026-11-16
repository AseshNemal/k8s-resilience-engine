/*
 * Failure impact page script. Owned by: Member 3 (K.V.D.Y.R. Karawita).
 *
 * One read (GET /api/failure-impacts/overview) answers for every worker node of the connected cluster's application:
 * which services lose their last ready replica, which workflows stop, and why. The page asks for nothing the cluster or
 * the approved/declared workflows already say: the application is the only one with workloads (or a dropdown), the
 * workflows are the ones that apply, and the node is the worst one until you choose another.
 *
 * All research values come from the Python engine. This file draws them. A missing value is "unknown", never zero.
 * Everything from the cluster (names, reasons) is untrusted and written with textContent only.
 * Nothing here writes to the cluster. The result is a prediction about a virtual failure, not an observation.
 */
(function () {
    "use strict";

    const OVERVIEW = "/api/failure-impacts/overview";
    const NAMESPACE_KEY = "resilience.failure-impact.namespace.v1";
    const view = window.ResilienceFailureView;
    const { fetchJson } = window.ResilienceApi;
    const { describeError, renderStateCard, applyBadge } = window.ResilienceConnection;
    const { initJsonPanel, updateLastRefreshed } = window.ResilienceUi;

    const $ = (role, root = document) => root.querySelector(`[data-role="${role}"]`);
    const region = $("page-region");
    const template = $("ready-template");
    const namespaceSelect = $("namespace-select");
    const evidenceBadge = $("evidence-badge");
    const basisBadge = $("basis-badge");
    const refreshButton = $("refresh-button");

    const state = { data: null, selected: null, namespace: "", workflowIds: [], generation: 0 };

    function el(tag, text, className, attributes) {
        const node = document.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = text;
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function badge(descriptor, extra) {
        return el("span", descriptor.text, `status-badge status-badge--${descriptor.variant}${extra ? " " + extra : ""}`);
    }

    function remember(key, value) { try { window.localStorage.setItem(key, value); } catch (_error) { /* optional */ } }
    function recall(key) { try { return window.localStorage.getItem(key) || ""; } catch (_error) { return ""; } }

    function time(iso) {
        const date = new Date(iso);
        return Number.isNaN(date.getTime()) ? "" : date.toLocaleTimeString();
    }

    // ---- header ----------------------------------------------------------------------------------------------------

    function fillNamespaces(names, chosen) {
        namespaceSelect.replaceChildren();
        if (!chosen) {
            namespaceSelect.appendChild(el("option", names.length ? "Choose an application…" : "No application found", "", { value: "" }));
        }
        for (const name of names) {
            const option = el("option", name, "", { value: name });
            if (name === chosen) option.selected = true;
            namespaceSelect.appendChild(option);
        }
        namespaceSelect.disabled = names.length === 0;
    }

    function describeEvidence(data) {
        if (data.state !== "ready") return { variant: "info", text: "Live cluster" };
        const when = time(data.capturedAt);
        if (data.topology.complete) return { variant: "success", text: `Live cluster · placement complete${when ? " · " + when : ""}` };
        return { variant: "warning", text: `Live cluster · placement partly unresolved${when ? " · " + when : ""}` };
    }

    // ---- node list -------------------------------------------------------------------------------------------------

    function renderNodeList(container, data) {
        container.replaceChildren();
        for (const node of data.nodes) {
            const button = el("button", null, `fi-node fi-node--${node.verdict}`, {
                type: "button", "aria-pressed": String(node.node === state.selected), "data-node": node.node,
            });
            const top = el("span", null, "fi-node__top");
            top.appendChild(el("span", `#${node.rank}`, "fi-node__rank"));
            top.appendChild(el("span", node.node, "fi-node__name"));
            button.appendChild(top);
            button.appendChild(badge(view.verdict(node.verdict), "fi-node__badge"));
            button.appendChild(el("span", view.cardLine(node), "fi-node__line"));
            button.addEventListener("click", () => select(node.node));
            container.appendChild(button);
        }
    }

    // ---- detail ----------------------------------------------------------------------------------------------------

    function dots(service) {
        const { dots: list, hidden } = view.replicaDots(service);
        const wrap = el("span", null, "fi-dots", { role: "img",
            "aria-label": `${service.readyBefore} ready replicas before, ${service.readyAfter} after` });
        for (const kind of list) wrap.appendChild(el("span", null, `fi-dot fi-dot--${kind}`));
        if (hidden > 0) wrap.appendChild(el("span", `+${hidden}`, "fi-dots__more"));
        return wrap;
    }

    function renderServices(container, node) {
        container.replaceChildren();
        const affected = node.services.filter((s) => s.outcome === "failed" || s.outcome === "degraded");
        const rest = node.services.filter((s) => !affected.includes(s));
        if (!affected.length) container.appendChild(el("p", "No service of this application has a ready replica on this node.", "muted"));
        for (const service of affected) {
            const row = el("div", null, `fi-service fi-service--${service.outcome}`);
            const head = el("div", null, "fi-service__head");
            head.appendChild(el("strong", service.service));
            head.appendChild(badge(view.serviceOutcome(service.outcome)));
            row.appendChild(head);
            const body = el("div", null, "fi-service__body");
            body.appendChild(dots(service));
            body.appendChild(el("span", `${service.readyBefore} → ${service.readyAfter} ready`, "fi-service__count"));
            row.appendChild(body);
            row.appendChild(el("p", service.reason, "fi-service__reason muted"));
            container.appendChild(row);
        }
        if (rest.length) {
            container.appendChild(el("p", `Unaffected: ${rest.map((s) => s.service).join(", ")}.`, "fi-rest muted"));
        }
    }

    function renderWorkflows(container, title, node, data) {
        container.replaceChildren();
        const workflows = node.workflows;
        title.textContent = workflows.length ? `Workflows (${workflows.length})` : "Workflows";
        if (!workflows.length) {
            container.appendChild(el("p", data.basis.text, "muted"));
            return;
        }
        for (const workflow of workflows) {
            const row = el("div", null, `fi-workflow fi-workflow--${workflow.survives ? "survives" : "stops"}`);
            const head = el("div", null, "fi-service__head");
            head.appendChild(el("strong", view.workflowLabel(workflow)));
            head.appendChild(badge(workflow.survives ? { variant: "success", text: "Keeps working" } : { variant: "danger", text: "Stops" }));
            if (workflow.kind === "declared") head.appendChild(el("span", "declared", "fi-tag", { title: "Declared by a person; not observed or approved." }));
            row.appendChild(head);
            row.appendChild(el("p", view.workflowLine(workflow), "fi-service__reason muted"));
            container.appendChild(row);
        }
    }

    function renderStats(container, node) {
        container.replaceChildren();
        for (const stat of view.nodeStats(node)) {
            const tile = el("div", null, `fi-stat fi-stat--${stat.tone}`);
            tile.appendChild(el("div", stat.value, "fi-stat__value"));
            tile.appendChild(el("div", stat.label, "fi-stat__label"));
            if (stat.hint) tile.setAttribute("title", stat.hint);
            container.appendChild(tile);
        }
    }

    function recommendationsHref() {
        const link = document.querySelector('.sidebar__link[href*="recommendations"]');
        return link ? link.getAttribute("href") : "/components/recommendations";
    }

    function renderRecommend(root, node, data) {
        const link = $("recommend-link", root);
        const note = $("recommend-note", root);
        const actionable = node.verdict === "workflow-lost" || node.verdict === "service-down";
        link.hidden = !actionable;
        link.setAttribute("href", recommendationsHref());
        link.onclick = () => {
            try {
                window.localStorage.setItem(view.HANDOFF_KEY, JSON.stringify(
                    view.handoffRecord(data.namespace, node.node, data.cluster.fingerprint)));
            } catch (_error) { /* The Recommendations page then asks again; nothing is lost. */ }
        };
        note.textContent = actionable
            ? "Opens Recommendations for this node and application."
            : "Nothing goes down when this node stops, so there is nothing to fix for it.";
    }

    function renderDetail(root, data) {
        const node = data.nodes.find((n) => n.node === state.selected) || data.nodes[0];
        $("detail-headline", root).textContent = node.headline;
        renderStats($("detail-stats", root), node);
        const caveat = $("detail-caveat", root);
        caveat.textContent = data.basis.kind === "frozen" || data.basis.kind === "none" ? "" : data.basis.text +
            " Whether a workflow really needs each service has not been tested on the cluster.";
        caveat.hidden = !caveat.textContent;
        renderServices($("detail-services", root), node);
        renderWorkflows($("detail-workflows", root), $("workflow-title", root), node, data);
        renderRecommend(root, node, data);
    }

    function select(name) {
        state.selected = name;
        const root = region;
        for (const button of root.querySelectorAll(".fi-node")) {
            button.setAttribute("aria-pressed", String(button.getAttribute("data-node") === name));
        }
        renderDetail(root, state.data);
        renderPlacement(root, state.data);
    }

    // ---- secondary panels --------------------------------------------------------------------------------------------

    function renderWorkflowPicker(root, data) {
        const picker = $("workflow-picker", root);
        const apply = $("apply-workflows", root);
        const list = data.basis.workflows;
        $("workflow-count", root).textContent = list.length ? `(${list.filter((w) => w.included).length} of ${list.length})` : "(none found)";
        $("workflow-basis-text", root).textContent = data.basis.text + (data.basis.problems.length ? " " + data.basis.problems.join(" ") : "");
        picker.replaceChildren();
        if (!list.length) {
            picker.appendChild(el("p", "No approved or declared workflow was found. Add one on Workflow dependencies to see which workflows would stop.", "muted"));
            return;
        }
        const initial = new Set(list.filter((w) => w.included).map((w) => w.workflowId));
        for (const workflow of list) {
            const label = el("label", null, "fi-pick");
            const box = el("input", null, "", { type: "checkbox", value: workflow.workflowId });
            box.checked = workflow.included;
            box.disabled = !workflow.applicable;
            label.appendChild(box);
            const text = el("span", null, "fi-pick__text");
            text.appendChild(el("strong", view.workflowLabel(workflow)));
            text.appendChild(el("span", workflow.kind === "declared" ? `declared by ${workflow.declaredBy || "a person"} · not observed` : "approved and frozen", "muted"));
            if (!workflow.applicable) text.appendChild(el("span", workflow.reason, "muted"));
            label.appendChild(text);
            box.addEventListener("change", () => {
                const chosen = new Set([...picker.querySelectorAll("input:checked")].map((input) => input.value));
                apply.hidden = chosen.size === initial.size && [...chosen].every((id) => initial.has(id));
            });
            picker.appendChild(label);
        }
        apply.onclick = () => {
            state.workflowIds = [...picker.querySelectorAll("input:checked")].map((input) => input.value);
            load();
        };
    }

    // The shared placement map: observed placement, with the selected node drawn as stopped.
    function renderPlacement(root, data) {
        window.ResiliencePlacement.draw($("placement-map", root), {
            nodes: data.nodes, placement: data.services || [], view: "today", failedNode: state.selected,
            onSelect: select,
        });
    }

    // ---- page --------------------------------------------------------------------------------------------------------

    function lead(data) {
        const risky = data.singlePointsOfFailure.length;
        const total = data.nodes.length;
        if (!risky) {
            return `No single worker node takes a service down on its own: every service of ${data.namespace} keeps a ready replica if any one node stops.`;
        }
        const worst = data.nodes[0];
        return `${risky} of ${total} worker nodes are a single point of failure for ${data.namespace}. The worst is ${worst.node}.`;
    }

    function renderReady(data) {
        const root = template.content.cloneNode(true);
        state.selected = view.chooseNode(data.nodes, state.selected);
        $("lead", root).textContent = lead(data);
        const notices = [];
        if (!data.topology.complete) {
            notices.push(`Placement is not fully known for: ${data.topology.incompleteServices.join(", ") || "part of the namespace"}. Those services are not assessed, so a node that looks harmless may not be.`);
        }
        const broken = data.workflowsAlreadyBroken || [];
        if (broken.length) {
            const detail = broken.map((b) => `${view.workflowLabel(b)}: ${b.services.join(", ")} has no ready replica`).join("; ");
            notices.push(`${broken.length === 1 ? "1 workflow is" : broken.length + " workflows are"} not working even before a node fails (${detail}). ` +
                `${broken.length === 1 ? "It is" : "They are"} left out of the counts below, so no node is blamed for it.`);
        }
        $("lead", root).after(...notices.map((text) => el("p", text, "fi-warning", { role: "status" })));
        renderNodeList($("node-list", root), data);
        renderDetail(root, data);
        renderWorkflowPicker(root, data);
        renderPlacement(root, data);
        const assumptions = $("assumptions", root);
        for (const line of data.assumptions) assumptions.appendChild(el("li", line));
        $("evidence-note", root).textContent =
            `Read from the connected cluster at ${data.capturedAt}. Topology status: ${data.topology.status}.` +
            (data.topology.warnings.length ? ` Notes: ${data.topology.warnings.join(", ")}.` : "") +
            " The result is a prediction, not an observation.";
        initJsonPanel($("fi-raw-json", root) || root.querySelector("#fi-raw-json"), data);
        region.replaceChildren(root);
        // select() reads the live DOM, so keep the pressed state in step with the model.
        for (const button of region.querySelectorAll(".fi-node")) {
            button.setAttribute("aria-pressed", String(button.getAttribute("data-node") === state.selected));
        }
    }

    function renderChoose(data) {
        region.replaceChildren(el("div", null, "state empty-state", { role: "status" }));
        const box = region.firstChild;
        if (data.state === "no-application") {
            box.appendChild(el("p", "No application found", "state__title"));
            box.appendChild(el("p", "The connected cluster has no namespace with workloads. Deploy an application, then refresh."));
        } else {
            box.appendChild(el("p", "Choose an application", "state__title"));
            box.appendChild(el("p", `${data.namespaces.length} namespaces hold applications, so none was chosen for you. Pick one above.`));
        }
    }

    function setBusy(busy) {
        refreshButton.disabled = busy;
        if (busy) region.setAttribute("aria-busy", "true"); else region.removeAttribute("aria-busy");
    }

    async function load() {
        const generation = ++state.generation;
        setBusy(true);
        const params = new URLSearchParams();
        if (state.namespace) params.set("namespace", state.namespace);
        for (const id of state.workflowIds) params.append("workflow_id", id);
        try {
            const data = await fetchJson(`${OVERVIEW}${params.toString() ? "?" + params.toString() : ""}`, { timeout: 60000 });
            if (generation !== state.generation) return;
            state.data = data;
            fillNamespaces(data.namespaces, data.namespace);
            applyBadge(evidenceBadge, describeEvidence(data));
            if (data.state === "ready") {
                state.namespace = data.namespace;
                remember(NAMESPACE_KEY, data.namespace);
                applyBadge(basisBadge, view.basisBadge(data.basis));
                renderReady(data);
            } else {
                applyBadge(basisBadge, { variant: "muted", text: "" });
                renderChoose(data);
            }
            updateLastRefreshed($("last-refreshed"));
        } catch (error) {
            if (generation !== state.generation) return;
            // A remembered application that no longer exists is not an error the person caused: ask again without it.
            if (error && error.code === "APPLICATION_NOT_FOUND" && state.namespace) {
                state.namespace = "";
                remember(NAMESPACE_KEY, "");
                return load();
            }
            applyBadge(evidenceBadge, { variant: "danger", text: "Not showing live data" });
            applyBadge(basisBadge, { variant: "muted", text: "" });
            namespaceSelect.disabled = true;
            renderStateCard(region, describeError(error), { onRetry: load });
        } finally {
            if (generation === state.generation) setBusy(false);
        }
    }

    namespaceSelect.addEventListener("change", () => {
        state.namespace = namespaceSelect.value;
        state.workflowIds = [];
        state.selected = null;
        load();
    });
    refreshButton.addEventListener("click", load);

    document.addEventListener("DOMContentLoaded", () => {
        state.namespace = recall(NAMESPACE_KEY);
        load();
    });
})();
