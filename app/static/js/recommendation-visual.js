/*
 * Pictures of what a recommendation does. Owned by: Member 4 (L.A. Nemal).
 *
 * A worker node is a box; a ready replica (pod) is a coloured square inside it, one colour per service. A recommendation is drawn as
 * the same nodes twice: NOW (observed) and AFTER the change (simulated, so its pods are dashed and labelled). When a node is picked as
 * the one that fails, it is drawn broken and every service that loses its pods there is called out, in both pictures, so the person
 * sees what the change buys without reading a table.
 *
 * It draws only what it is given (``placement`` rows from the cluster read; ``after`` from the engine's simulation). The failure rule
 * is the engine's (down = every ready replica was on the node, degraded = some), the same one placement-map.js applies. Nothing here is
 * measured or predicted by this script. All text goes through textContent.
 */
(function (global) {
    "use strict";

    const SVG = "http://www.w3.org/2000/svg";
    const POD = 18, GAP = 5, PAD = 8, HEAD = 22;

    // What each strategy does, in a sentence a newcomer can read, and the verb that goes on the card.
    const STRATEGY = {
        TOPOLOGY_SPREAD: { name: "Spread across nodes", icon: "spread", sentence: "Tells Kubernetes to place replicas on different nodes, as evenly as it can." },
        REPLICA_INCREASE: { name: "Add a replica", icon: "plus", sentence: "Runs one more copy of a service that currently has a single copy." },
        POD_ANTI_AFFINITY: { name: "Keep replicas apart", icon: "apart", sentence: "Forbids two replicas of the same service from sharing a node." },
        COMBINATION: { name: "Combined change", icon: "combo", sentence: "Several of the changes above, applied together." },
    };

    function strategyInfo(type) {
        const key = String(type || "");
        if (STRATEGY[key]) return STRATEGY[key];
        if (key.startsWith("REPLICA")) return STRATEGY.REPLICA_INCREASE;      // "Replica increase plus 2" and the like
        if (key.includes("ANTI_AFFINITY")) return STRATEGY.POD_ANTI_AFFINITY;
        return { name: String(type || "Change").replace(/_/g, " ").toLowerCase().replace(/^./, (c) => c.toUpperCase()), icon: "combo", sentence: "" };
    }

    // ---- pure helpers (exported for tests) --------------------------------------------------------------------------

    function readyOn(row, node) {
        const here = ((row && row.nodes) || []).find((entry) => entry.node === node);
        return here ? here.readyPods : 0;
    }

    function total(row) {
        return ((row && row.nodes) || []).reduce((sum, entry) => sum + entry.readyPods, 0);
    }

    /** What happens to one service when ``node`` stops: {state: down|degraded|ok|none, left, of}. The engine's rule. */
    function outcome(row, node) {
        const all = total(row);
        if (!all) return { state: "none", left: 0, of: 0 };
        const lost = node ? readyOn(row, node) : 0;
        if (lost === all) return { state: "down", left: 0, of: all };
        if (lost > 0) return { state: "degraded", left: all - lost, of: all };
        return { state: "ok", left: all, of: all };
    }

    function outcomeText(service, result) {
        if (result.state === "down") return `${service} goes offline`;
        if (result.state === "degraded") return `${service} keeps ${result.left} of ${result.of}`;
        if (result.state === "ok") return `${service} keeps all ${result.of}`;
        return `${service}: no ready replica`;
    }

    /** The services a change touches: its parts, else the service names joined by "+". */
    function touched(option) {
        const parts = option && option.parts && option.parts.length ? option.parts.map((p) => p.service) : String(option && option.service || "").split("+");
        return parts.map((s) => s.trim()).filter(Boolean);
    }

    function hueOf(name, names) {
        const sorted = [...new Set(names)].sort((a, b) => String(a).localeCompare(String(b)));
        return [212, 152, 32, 280, 350, 190, 95, 255, 15, 320][Math.max(0, sorted.indexOf(name)) % 10];
    }

    // ---- SVG ----------------------------------------------------------------------------------------------------------

    function s(doc, tag, attributes, text) {
        const node = doc.createElementNS ? doc.createElementNS(SVG, tag) : doc.createElement(tag);
        for (const [k, v] of Object.entries(attributes || {})) node.setAttribute(k, String(v));
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function h(doc, tag, className, text, attributes) {
        const node = doc.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = String(text);
        for (const [k, v] of Object.entries(attributes || {})) node.setAttribute(k, v);
        return node;
    }

    /**
     * One picture of the nodes with their pods. ``rows``: placement rows; ``focus``: the services to show in colour (the rest are grey
     * context); ``failed``: the node that stops (drawn broken), or null; ``simulated``: dashed pods; ``nodes``: worker names in order.
     */
    function scene(doc, { rows, nodes, focus, failed, simulated, hues, label }) {
        const count = Math.max(nodes.length, 1);
        const W = 280, nodeW = Math.min(92, Math.floor((W - GAP * (count + 1)) / count)), cols = Math.max(1, Math.floor((nodeW - PAD) / (POD + GAP)));
        const stacks = nodes.map((node) => rows.flatMap((row) => Array.from({ length: readyOn(row, node) }, () => row.service)));
        const most = Math.max(1, ...stacks.map((list) => Math.ceil(list.length / cols)));
        const nodeH = HEAD + PAD + most * (POD + GAP) + 6, H = nodeH + 8;
        const left = Math.round((W - (nodeW * count + GAP * (count - 1))) / 2);
        const svg = s(doc, "svg", { viewBox: `0 0 ${W} ${H}`, class: "rv-scene", role: "img", "aria-label": label });
        nodes.forEach((node, index) => {
            const x = left + index * (nodeW + GAP), isFailed = node === failed;
            const g = s(doc, "g", { class: `rv-node${isFailed ? " rv-node--failed" : ""}` });
            g.append(s(doc, "rect", { x, y: 4, width: nodeW, height: nodeH, rx: 8, class: "rv-node__box" }));
            g.append(s(doc, "text", { x: x + nodeW / 2, y: 18, "text-anchor": "middle", class: "rv-node__name" }, node.length > 12 ? `${node.slice(0, 11)}…` : node));
            stacks[index].forEach((service, i) => {
                const px = x + PAD / 2 + (i % cols) * (POD + GAP) + 2, py = 4 + HEAD + Math.floor(i / cols) * (POD + GAP);
                const colored = !focus || focus.includes(service);
                const hue = hues[service] === undefined ? 210 : hues[service];
                const pod = s(doc, "rect", { x: px, y: py, width: POD, height: POD, rx: 3,
                    class: `rv-pod${simulated ? " rv-pod--sim" : ""}${colored ? "" : " rv-pod--ctx"}${isFailed ? " rv-pod--lost" : ""}` });
                if (colored) pod.setAttribute("style", `--rv-hue:${hue}`);
                const tip = s(doc, "title", {}, `${service}${simulated ? " (simulated)" : ""}`);
                pod.append(tip);
                g.append(pod);
            });
            if (isFailed) {
                g.append(s(doc, "path", { d: `M${x + 8} ${nodeH - 6} L${x + nodeW - 8} 14 M${x + nodeW - 8} ${nodeH - 6} L${x + 8} 14`, class: "rv-cross" }));
            }
            svg.append(g);
        });
        return svg;
    }

    function chips(doc, rows, focus, failed) {
        const list = h(doc, "ul", "rv-chips");
        (focus || rows.map((r) => r.service)).forEach((service) => {
            const row = rows.find((r) => r.service === service);
            if (!row) return;
            const result = outcome(row, failed);
            const mark = result.state === "down" ? "✕" : result.state === "degraded" ? "⚠" : "✓";
            const chip = h(doc, "li", `rv-chip rv-chip--${result.state}`);
            chip.append(h(doc, "span", "rv-chip__mark", mark, { "aria-hidden": "true" }), h(doc, "span", "", failed ? outcomeText(service, result) : `${service}: ${total(row)} replicas`));
            list.append(chip);
        });
        return list;
    }

    function legend(doc, services, hues) {
        const list = h(doc, "ul", "rv-legend");
        services.forEach((name) => {
            const item = h(doc, "li");
            const dot = h(doc, "i", "rv-legend__dot", undefined, { "aria-hidden": "true" });
            dot.setAttribute("style", `--rv-hue:${hues[name] === undefined ? 210 : hues[name]}`);
            item.append(dot, h(doc, "span", "", name));
            list.append(item);
        });
        return list;
    }

    function icon(doc, kind) {
        const svg = s(doc, "svg", { viewBox: "0 0 24 24", class: "rv-icon", "aria-hidden": "true" });
        const paths = {
            spread: "M4 18h4V8H4zM10 18h4V4h-4zM16 18h4v-6h-4z", plus: "M12 5v14M5 12h14", apart: "M4 6h6v12H4zM14 6h6v12h-6zM11 12h2",
            combo: "M5 5h6v6H5zM13 13h6v6h-6zM13 5h6v6h-6z",
        };
        svg.append(s(doc, "path", { d: paths[kind] || paths.combo, class: "rv-icon__path", fill: "none" }));
        return svg;
    }

    /**
     * The picture for one recommendation: the same nodes NOW and AFTER the change, with the chosen node broken in both.
     * ``option``: {parts|service, type, afterPlacement}; ``before``: observed placement rows; ``nodes``: worker names; ``failed``: node or null.
     */
    function changeCard(doc, option, before, nodes, failed) {
        const after = option.afterPlacement || null;
        const focus = touched(option).filter((name) => (before || []).some((row) => row.service === name));
        const everyone = [...new Set([...(before || []).map((r) => r.service), ...((after || []).map((r) => r.service))])];
        const hues = {};
        everyone.forEach((name) => { hues[name] = hueOf(name, everyone); });
        const figure = h(doc, "figure", "rv-card");
        const panes = h(doc, "div", "rv-panes");
        const sets = [["Now", before || [], false, "observed"], ["After the change", after, true, "simulated"]];
        sets.forEach(([title, rows, simulated, kind], index) => {
            if (index === 1) panes.append(h(doc, "div", "rv-arrow", "→", { "aria-hidden": "true" }));
            const pane = h(doc, "div", `rv-pane rv-pane--${kind}`);
            const head = h(doc, "div", "rv-pane__head");
            head.append(h(doc, "strong", "", title), h(doc, "span", `rv-tag rv-tag--${kind}`, kind === "simulated" ? "Simulated" : "Observed"));
            pane.append(head);
            if (!rows) { pane.append(h(doc, "p", "rv-empty", "The engine did not return a placement for this change.")); panes.append(pane); return; }
            const label = `${title}: ${focus.map((name) => { const row = rows.find((r) => r.service === name); return row ? `${name} on ${(row.nodes || []).map((n) => `${n.node} (${n.readyPods})`).join(", ")}` : name; }).join("; ")}`;
            pane.append(scene(doc, { rows, nodes, focus, failed, simulated, hues, label }), chips(doc, rows, focus, failed));
            panes.append(pane);
        });
        figure.append(panes, legend(doc, focus.length ? focus : everyone, hues));
        const note = failed
            ? `Broken node = ${failed} stops. Pods inside it are lost.`
            : "Pick a worker above to see what happens if it stops.";
        figure.append(h(doc, "figcaption", "rv-note", note));
        return figure;
    }

    /** The title block of a recommendation card: strategy icon, a plain name, and what the strategy does. */
    function strategyHead(doc, option) {
        const info = strategyInfo(option.type);
        const box = h(doc, "div", "rv-strategy");
        box.append(icon(doc, info.icon));
        const text = h(doc, "div");
        text.append(h(doc, "strong", "rv-strategy__name", info.name), h(doc, "span", "rv-strategy__what", info.sentence));
        box.append(text);
        return box;
    }

    global.ResilienceVisual = { changeCard, strategyHead, strategyInfo, outcome, outcomeText, touched, readyOn, scene };
})(typeof window !== "undefined" ? window : globalThis);
