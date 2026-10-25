/*
 * Before / after topology view for the Recommendations page. Owned by: Member 4.
 *
 * Draws what the engine supplied and nothing else: the DeploymentGraph the candidates were evaluated on ("now") and
 * the virtual DeploymentGraph its simulation produced ("after the change"). Services are on the left, worker nodes on
 * the right, pods are chips on the node that holds them. A pod is compared by name between the two graphs: the same
 * name on another node moved, a name only in the simulation is added, a name only in the current graph is removed.
 * Nothing is estimated here: no capacity is drawn (the graph does not report one), and the failure survival is the
 * server's, not recomputed in the browser. Without a simulated graph only "now" is drawn and the page says so.
 */
(function (global) {
    "use strict";

    const COLOURS = 8;

    function field(object, camel, snake) {
        if (!object) return undefined;
        return object[camel] !== undefined ? object[camel] : object[snake];
    }

    function element(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = text;
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function servicesOf(graph) {
        return graph && Array.isArray(graph.services) ? graph.services : [];
    }

    function podsOf(service) {
        return service && Array.isArray(service.pods) ? service.pods : [];
    }

    /** Node names in the order the graph lists them, then any node that only a pod names. */
    function nodeNames(graphs) {
        const names = [];
        const add = (name) => { if (typeof name === "string" && name && !names.includes(name)) names.push(name); };
        for (const graph of graphs) {
            for (const node of (graph && graph.nodes) || []) add(node.name);
        }
        for (const graph of graphs) {
            for (const service of servicesOf(graph)) podsOf(service).forEach((pod) => add(pod.node));
        }
        return names;
    }

    /**
     * The comparison as plain data. `simulated` may be null. Returns null when there is no current graph, because
     * a comparison needs something to compare against.
     */
    function buildModel(current, simulated, options) {
        if (!current || !Array.isArray(current.services)) return null;
        const failedNode = (options && options.failedNode) || null;
        const graphs = [current, simulated].filter(Boolean);
        const serviceNames = [];
        for (const graph of graphs) {
            for (const service of servicesOf(graph)) {
                if (!serviceNames.includes(service.name)) serviceNames.push(service.name);
            }
        }
        const colourOf = (name) => serviceNames.indexOf(name) % COLOURS;

        const index = (graph) => {
            const pods = new Map();
            for (const service of servicesOf(graph)) {
                for (const pod of podsOf(service)) pods.set(`${service.name}\u0000${pod.name}`, pod);
            }
            return pods;
        };
        const nowPods = index(current);
        const afterPods = simulated ? index(simulated) : null;

        const serviceRow = (graph, name) => {
            const service = servicesOf(graph).find((item) => item.name === name);
            if (!service) return null;
            return {
                declared: field(service, "declaredReplicas", "declared_replicas"),
                ready: field(service, "readyReplicas", "ready_replicas"),
                pods: podsOf(service).length,
            };
        };

        const chip = (service, pod, other, side) => {
            const mate = other ? other.get(`${service.name}\u0000${pod.name}`) : null;
            let state = "stays";
            if (side === "now" && other && !mate) state = "removed";
            if (side === "after" && other && !mate) state = "added";
            if (mate && mate.node !== pod.node) state = "moves";
            return {
                pod: pod.name, service: service.name, colour: colourOf(service.name), node: pod.node,
                ready: pod.ready !== false, state, otherNode: mate && mate.node !== pod.node ? mate.node : null,
            };
        };

        const side = (graph, other, which) => {
            const names = nodeNames(graphs);
            const nodes = names.map((name) => {
                const declared = ((graph && graph.nodes) || []).find((node) => node.name === name) || null;
                const chips = [];
                for (const service of servicesOf(graph)) {
                    for (const pod of podsOf(service)) {
                        if (pod.node === name) chips.push(chip(service, pod, other, which));
                    }
                }
                return {
                    name, chips, failed: failedNode === name,
                    ready: declared ? declared.ready : null,
                    schedulable: declared ? declared.schedulable : null,
                };
            });
            return { nodes };
        };

        const now = side(current, afterPods, "now");
        const after = simulated ? side(simulated, nowPods, "after") : null;
        const changes = { moved: 0, added: 0, removed: 0 };
        if (after) {
            for (const node of after.nodes) {
                for (const item of node.chips) {
                    if (item.state === "moves") changes.moved += 1;
                    if (item.state === "added") changes.added += 1;
                }
            }
            for (const node of now.nodes) {
                for (const item of node.chips) if (item.state === "removed") changes.removed += 1;
            }
        }
        return {
            failedNode,
            services: serviceNames.map((name) => ({
                name, colour: colourOf(name), now: serviceRow(current, name), after: simulated ? serviceRow(simulated, name) : null,
            })),
            now, after, changes,
        };
    }

    function countText(row) {
        if (!row) return "—";
        if (row.ready === undefined || row.ready === null || row.declared === undefined || row.declared === null) return String(row.pods);
        return `${row.ready}/${row.declared}`;
    }

    function chipTitle(item, which) {
        const parts = [`${item.service} · ${item.pod}`];
        if (!item.ready) parts.push("not ready");
        if (item.state === "moves") parts.push(which === "now" ? `moves to ${item.otherNode}` : `moved from ${item.otherNode}`);
        if (item.state === "added") parts.push("added by the change");
        if (item.state === "removed") parts.push("removed by the change");
        return parts.join(" — ");
    }

    function renderSide(doc, model, which) {
        const data = model[which];
        const panel = element(doc, "div", undefined, "rec-topo__panel", { "data-side": which });
        panel.append(element(doc, "div", which === "now" ? "Now (observed)" : "After the change (predicted)", "rec-topo__title"));
        const body = element(doc, "div", undefined, "rec-topo__body");

        const services = element(doc, "div", undefined, "rec-topo__services");
        services.append(element(doc, "div", "Services", "rec-topo__label"));
        for (const service of model.services) {
            const row = which === "now" ? service.now : service.after;
            const box = element(doc, "div", undefined, "rec-topo__service", { "data-colour": String(service.colour), "data-service": service.name });
            box.append(element(doc, "span", undefined, "rec-topo__swatch"), element(doc, "span", service.name, "rec-topo__service-name"),
                element(doc, "span", countText(row), "rec-topo__count"));
            services.append(box);
        }

        const nodes = element(doc, "div", undefined, "rec-topo__nodes");
        nodes.append(element(doc, "div", "Worker nodes", "rec-topo__label"));
        for (const node of data.nodes) {
            const card = element(doc, "div", undefined, "rec-topo__node", { "data-node": node.name, "data-failed": String(node.failed) });
            const head = element(doc, "div", undefined, "rec-topo__node-head");
            head.append(element(doc, "span", node.name, "rec-topo__node-name"));
            if (node.failed) head.append(element(doc, "span", "assessed failure", "rec-topo__badge rec-topo__badge--failed"));
            if (node.ready === false) head.append(element(doc, "span", "not ready", "rec-topo__badge"));
            card.append(head);
            const chips = element(doc, "div", undefined, "rec-topo__chips");
            if (node.chips.length === 0) chips.append(element(doc, "span", "no pods", "rec-topo__empty"));
            for (const item of node.chips) {
                chips.append(element(doc, "span", item.service, "rec-topo__chip", {
                    "data-colour": String(item.colour), "data-state": item.state, "data-ready": String(item.ready),
                    "data-service": item.service, title: chipTitle(item, which),
                }));
            }
            card.append(chips);
            nodes.append(card);
        }
        body.append(services, nodes);
        panel.append(body);
        return panel;
    }

    function summaryText(model) {
        if (!model.after) return "No predicted placement was supplied for this choice, so only the observed placement is drawn.";
        const { moved, added, removed } = model.changes;
        if (moved + added + removed === 0) return "The predicted placement is the same as the observed one.";
        const parts = [];
        if (moved) parts.push(`${moved} pod${moved === 1 ? "" : "s"} move`);
        if (added) parts.push(`${added} added`);
        if (removed) parts.push(`${removed} removed`);
        return `Predicted change: ${parts.join(", ")}. A prediction from the simulation, not a measurement.`;
    }

    /** Draws the comparison into `container`. Returns the model, or null when there is nothing to compare. */
    function render(container, current, simulated, options) {
        const doc = (options && options.document) || global.document;
        container.replaceChildren();
        const model = buildModel(current, simulated, options);
        if (!model) {
            container.append(element(doc, "p", "No topology evidence was supplied, so there is nothing to compare.", "ui-note"));
            return null;
        }
        container.append(element(doc, "p", summaryText(model), "rec-topo__summary", { role: "status" }));
        const grid = element(doc, "div", undefined, "rec-topo__grid");
        grid.append(renderSide(doc, model, "now"));
        if (model.after) grid.append(renderSide(doc, model, "after"));
        container.append(grid);
        const legend = element(doc, "p", "Dashed: pod not ready. Ringed: moved or added by the change. Struck through: on the node whose failure is assessed.", "rec-topo__legend");
        container.append(legend);
        if (doc.createElementNS) scheduleLinks(grid);
        return model;
    }

    // ---- curves: drawn from where the boxes ended up, so they need a layout and are skipped without one ----------------

    function drawLinks(panel) {
        const doc = panel.ownerDocument;
        const box = panel.getBoundingClientRect();
        if (!box || !box.width) return;
        let svg = panel.querySelector(".rec-topo__links");
        if (svg) svg.remove();
        svg = doc.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", "rec-topo__links");
        svg.setAttribute("aria-hidden", "true");
        svg.setAttribute("width", String(box.width));
        svg.setAttribute("height", String(box.height));
        const seen = new Map();
        for (const chip of panel.querySelectorAll(".rec-topo__chip")) {
            const service = panel.querySelector(`.rec-topo__service[data-service="${chip.getAttribute("data-service")}"]`);
            const node = chip.closest(".rec-topo__node");
            if (!service || !node) continue;
            const key = `${chip.getAttribute("data-service")}\u0000${node.getAttribute("data-node")}`;
            const order = seen.get(key) || 0;
            seen.set(key, order + 1);
            if (order > 0) continue;                     // one curve per service and node, however many pods
            const from = service.getBoundingClientRect();
            const to = node.getBoundingClientRect();
            const x1 = from.right - box.left;
            const y1 = from.top + from.height / 2 - box.top;
            const x2 = to.left - box.left;
            const y2 = to.top + Math.min(to.height / 2, 18 + (seen.size % 4) * 6) - box.top;   // spread the landing points a little
            const mid = (x1 + x2) / 2;
            const path = doc.createElementNS("http://www.w3.org/2000/svg", "path");
            path.setAttribute("d", `M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`);
            path.setAttribute("class", "rec-topo__link");
            path.setAttribute("data-colour", chip.getAttribute("data-colour"));
            svg.append(path);
        }
        panel.prepend(svg);
    }

    function scheduleLinks(grid) {
        const draw = () => grid.querySelectorAll(".rec-topo__body").forEach(drawLinks);
        const later = global.requestAnimationFrame || ((fn) => global.setTimeout(fn, 0));
        later(draw);
        if (global.ResizeObserver) new global.ResizeObserver(draw).observe(grid);
    }

    global.ResilienceTopologyCompare = { buildModel, render, summaryText };
})(typeof window !== "undefined" ? window : globalThis);
