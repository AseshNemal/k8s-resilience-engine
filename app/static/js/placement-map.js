/*
 * Placement map: which worker node each service's ready replicas run on, and what a single node failure does to them.
 *
 * One shared picture for Assessment and Failure impact. It draws only what it is given:
 *   placement  [{service, readyReplicas, nodes: [{node, readyPods}]}]   observed, from the cluster read
 *   after      the same shape, SIMULATED by Recommendations for one change (never observed)
 * The failure itself is the same rule the engine uses (a service is down when every ready replica was on the
 * stopped node, degraded when only some were), applied to the placement it is shown. Nothing is measured here.
 * Text is assigned with textContent.
 */
(function (global) {
    "use strict";

    const HUES = [212, 152, 32, 280, 350, 190, 95, 255, 15, 320];   // same list and order as the Deployment topology widget

    /** One fixed hue per service, by sorted name, so a service keeps its colour on every page. */
    function hues(names) {
        const map = {};
        [...new Set(names)].sort((a, b) => String(a).localeCompare(String(b))).forEach((name, index) => { map[name] = HUES[index % HUES.length]; });
        return map;
    }

    function podsOn(row, node) {
        const here = ((row && row.nodes) || []).find((entry) => entry.node === node);
        return here ? here.readyPods : 0;
    }

    function totalPods(row) {
        return ((row && row.nodes) || []).reduce((sum, entry) => sum + entry.readyPods, 0);
    }

    /** "down" | "degraded" | "ok" | "none" for one service if `node` stopped. `none` means it has no ready replica today. */
    function outcome(row, node) {
        const total = totalPods(row);
        if (!total) return { state: "none", left: 0, of: 0 };
        const lost = node ? podsOn(row, node) : 0;
        if (lost === total) return { state: "down", left: 0, of: total };
        if (lost > 0) return { state: "degraded", left: total - lost, of: total };
        return { state: "ok", left: total, of: total };
    }

    /** What stopping `node` does to every service in `placement`. */
    function impact(placement, node) {
        const down = [], degraded = [];
        for (const row of placement || []) {
            const result = outcome(row, node);
            if (result.state === "down") down.push(row.service);
            else if (result.state === "degraded") degraded.push(row.service);
        }
        return { down, degraded };
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function plural(count, one, many) { return `${count} ${count === 1 ? one : many || `${one}s`}`; }

    function statusText(result, failing) {
        if (result.state === "none") return "No ready replica";
        if (!failing) return plural(result.of, "replica");
        if (result.state === "down") return "Offline";
        if (result.state === "degraded") return `${result.left} of ${result.of} left`;
        return "Running";
    }

    function statusCell(row, afterRow, failedNode, view) {
        const now = outcome(row, failedNode);
        const cell = el("div", "pm-state");
        if (view === "after" && afterRow) {
            const next = outcome(afterRow, failedNode);
            cell.dataset.state = next.state;
            if (now.state !== next.state || now.left !== next.left) {
                // Red only for a new outage. A service that stays up with fewer replicas lost headroom, not availability (amber).
                const order = ["ok", "degraded", "down"];
                const rank = (state) => order.indexOf(state);
                cell.dataset.change = next.state === "down" && now.state !== "down" ? "worse"
                    : rank(next.state) > rank(now.state) || (next.state === now.state && next.left < now.left) ? "less"
                        : "better";
                cell.textContent = `${statusText(now, true)} → ${statusText(next, true)}`;
            } else {
                cell.textContent = statusText(next, Boolean(failedNode));
            }
            return cell;
        }
        cell.dataset.state = failedNode ? now.state : "idle";
        cell.textContent = statusText(now, Boolean(failedNode));
        return cell;
    }

    function pods(row, afterRow, node, failedNode, view, hue) {
        const box = el("div", "pm-cell");
        box.dataset.node = node;
        if (node === failedNode) box.dataset.failing = "true";
        const now = podsOn(row, node);
        const next = afterRow ? podsOn(afterRow, node) : now;
        const shown = view === "after" && afterRow ? next : now;
        const added = view === "after" && afterRow ? Math.max(0, next - now) : 0;
        const kept = shown - added;
        const removed = view === "after" && afterRow ? Math.max(0, now - next) : 0;
        const stopped = node === failedNode;
        const draw = (kind, count) => {
            for (let index = 0; index < count; index += 1) {
                const pod = el("span", "pm-pod");
                pod.style.setProperty("--hue", hue);
                pod.dataset.kind = kind;
                if (stopped && kind !== "gone") pod.dataset.lost = "true";
                box.append(pod);
            }
        };
        draw("kept", kept);
        draw("new", added);
        draw("gone", removed);
        const parts = [];
        if (shown) parts.push(plural(shown, "replica"));
        if (added) parts.push(`${added} new (simulated)`);
        if (removed) parts.push(`${removed} moved away (simulated)`);
        if (stopped && shown) parts.push("lost if this worker stops");
        box.setAttribute("aria-label", `${row.service} on ${node}: ${parts.join(", ") || "none"}`);
        return box;
    }

    /**
     * Draw the map into `container`.
     * options: { nodes: [name | {node}], placement, after, view: "today"|"after", failedNode, onSelect(node) }
     */
    function draw(container, options) {
        const placement = options.placement || [];
        const view = options.view === "after" && options.after ? "after" : "today";
        const after = view === "after" ? options.after : null;
        const nodeNames = (options.nodes || []).map((item) => (typeof item === "string" ? item : item.node)).sort((a, b) => a.localeCompare(b));
        const afterBy = Object.fromEntries((after || []).map((row) => [row.service, row]));
        const hue = hues(placement.map((row) => row.service));
        const columns = `minmax(120px, 1.2fr) repeat(${Math.max(nodeNames.length, 1)}, minmax(76px, 1fr)) minmax(132px, 1fr)`;
        container.replaceChildren();
        container.classList.add("pm");
        container.dataset.view = view;

        const grid = el("div", "pm-grid");
        grid.setAttribute("role", "group");
        grid.setAttribute("aria-label", "Where each service runs");
        grid.style.setProperty("--pm-cols", columns);

        const head = el("div", "pm-row pm-row--head");
        head.append(el("div", "pm-corner", "Service"));
        for (const name of nodeNames) {
            const failing = name === options.failedNode;
            const effect = impact(view === "after" ? after : placement, name);
            const tone = effect.down.length ? "bad" : effect.degraded.length ? "warn" : "ok";
            const button = el("button", "pm-node");
            button.type = "button";
            button.dataset.tone = tone;
            button.dataset.failing = failing ? "true" : "false";
            button.setAttribute("aria-pressed", failing ? "true" : "false");
            button.title = `Simulate stopping ${name}`;
            const hint = failing ? "Stopped (simulated)"
                : effect.down.length ? `${effect.down.length} offline if it stops`
                    : effect.degraded.length ? `${effect.degraded.length} reduced if it stops`
                        : "Safe to lose";
            button.append(el("strong", "pm-node__name", name), el("span", "pm-node__hint", hint));
            button.addEventListener("click", () => { if (options.onSelect) options.onSelect(name); });
            head.append(button);
        }
        head.append(el("div", "pm-corner pm-corner--end", options.failedNode ? "If it stops" : "Replicas"));
        grid.append(head);

        for (const row of placement) {
            const afterRow = afterBy[row.service] || null;
            const line = el("div", "pm-row");
            const label = el("div", "pm-svc");
            label.style.setProperty("--hue", hue[row.service]);
            label.append(el("i", "pm-swatch"), el("span", "pm-svc__name", row.service));
            line.append(label);
            for (const name of nodeNames) line.append(pods(row, afterRow, name, options.failedNode, view, hue[row.service]));
            line.append(statusCell(row, afterRow, options.failedNode, view));
            grid.append(line);
        }
        container.append(grid);

        const legend = el("p", "pm-legend");
        const items = [["kept", "Running replica"], ["lost", "Lost if this worker stops"]];
        if (view === "after") items.push(["new", "New replica (simulated)"]);
        for (const [kind, text] of items) {
            const item = el("span", "pm-legend__item");
            const sample = el("i", "pm-pod pm-pod--sample");
            sample.dataset.kind = kind === "new" ? "new" : "kept";
            if (kind === "lost") sample.dataset.lost = "true";
            sample.style.setProperty("--hue", 212);
            item.append(sample, document.createTextNode(text));
            legend.append(item);
        }
        container.append(legend);
    }

    /** Workflow pills for one stopped node. `workflows` is the engine's per-node list; nothing is guessed. */
    function drawWorkflows(container, workflows) {
        container.replaceChildren();
        if (!workflows || !workflows.length) { container.hidden = true; return; }
        container.hidden = false;
        container.append(el("span", "pm-wf__label", "Workflows"));
        for (const flow of workflows) {
            const pill = el("span", "pm-wf", flow.name || flow.workflowId);
            pill.dataset.state = flow.survives ? "ok" : "bad";
            pill.title = flow.survives ? "Keeps working" : `Stops: ${(flow.failedMandatoryServices || []).join(", ")} unavailable`;
            container.append(pill);
        }
    }

    /** The same rows from a full DeploymentGraph (observed pods): ready pods per node, per service. */
    function fromDeployment(graph) {
        return ((graph && graph.services) || []).map((service) => {
            const perNode = {};
            for (const pod of service.pods || []) {
                if (pod.ready && pod.node) perNode[pod.node] = (perNode[pod.node] || 0) + 1;
            }
            const nodes = Object.keys(perNode).sort().map((node) => ({ node, readyPods: perNode[node] }));
            return { service: service.name, readyReplicas: nodes.reduce((sum, entry) => sum + entry.readyPods, 0), nodes };
        }).sort((a, b) => String(a.service).localeCompare(String(b.service)));
    }

    global.ResiliencePlacement = { hues, outcome, impact, draw, drawWorkflows, fromDeployment };
})(typeof window !== "undefined" ? window : globalThis);
