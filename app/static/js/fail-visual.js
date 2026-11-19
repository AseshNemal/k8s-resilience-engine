/*
 * Pictures for the "Fail a node" page. Owned by: Member 4 (L.A. Nemal).
 *
 * Draws only what it is given: each worker node as a small server with one square per READY replica that runs on it (the observed
 * placement), which nodes are picked, and, while a test runs, which nodes the cluster reports as stopped. Nothing here is predicted or
 * measured by itself; a node whose state is unknown is drawn as unknown, never as running. All text goes through textContent.
 */
(function (global) {
    "use strict";

    const SERVER = '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" aria-hidden="true"><rect x="3.5" y="4" width="17" height="6.5" rx="1.2" stroke="currentColor" stroke-width="1.6"/><rect x="3.5" y="13.5" width="17" height="6.5" rx="1.2" stroke="currentColor" stroke-width="1.6"/><circle cx="7" cy="7.25" r="1" fill="currentColor"/><circle cx="7" cy="16.75" r="1" fill="currentColor"/></svg>';
    const STATE_TEXT = { pick: "Tap to stop", picked: "Will stop", up: "Running", down: "Stopped", keep: "Keeps running", unknown: "State unknown", locked: "Never stopped here",
        stopping: "Stopping…", held: "Stopped (cluster not updated yet)", returning: "Starting again…" };

    // ---- pure (exported for tests) -----------------------------------------------------------------------------------------------

    /**
     * Where a running test is, from the run itself: "stopping" (the workers are being stopped now), "holding" (they were stopped and are being
     * held), "restoring" (they are being started again) or "other". The cluster is slower than the test: Kubernetes keeps reporting a stopped
     * node as Ready for about a minute, so the test's own stage, not the cluster's node state, says what is happening to a worker.
     */
    function runPhase(run) {
        if (!run) return "other";
        if (run.status === "ABORTING") return "restoring";
        const stage = String((run.lab && run.lab.stage) || run.stage || "").toUpperCase();
        if (stage === "INJECT_FAILURE") return "stopping";
        if (stage === "PROBE") return "holding";
        if (stage === "RECOVER_WORKER") return "restoring";
        return "other";
    }

    /**
     * What a node tile says: picking, picked, or (while a test runs) stopping / held / down / returning / up / unknown. A control plane is locked.
     * ``phase`` is ``runPhase``; without it a running tile follows the cluster's reading alone. ``held`` is a worker the test has stopped that the
     * cluster still reports as Ready: drawn as stopped (it is), and labelled as not yet seen by Kubernetes, never as "Running".
     */
    function tileState(node, { picked, stopped, running, locked, preview, phase }) {
        if (locked) return "locked";
        if (running) {
            if (phase === "stopping") return "stopping";
            if (phase === "holding") return stopped === true ? "down" : "held";
            if (phase === "restoring") return stopped === false ? "up" : "returning";
            return stopped === true ? "down" : stopped === false ? "up" : "unknown";
        }
        if (picked) return "picked";
        return preview ? "keep" : "pick";
    }

    /** True while the cluster's own readings cannot be trusted yet: a test is stopping or holding workers and not every watched worker reads NotReady. */
    function awaitingCluster(run) {
        const phase = runPhase(run);
        if (phase !== "stopping" && phase !== "holding") return false;
        const nodes = liveNodes(run);
        const watched = (run.lab && run.lab.nodes) || [];
        return !watched.length || watched.some((name) => nodes[name] !== false);
    }

    /** The pods of one node: [{service, count}] from the placement rows ({service, nodes:[{node, readyPods}]}). */
    function podsOf(placement, node) {
        const out = [];
        for (const row of placement || []) {
            const here = (row.nodes || []).find((entry) => entry.node === node);
            if (here && here.readyPods > 0) out.push({ service: row.service, count: here.readyPods });
        }
        return out;
    }

    /** The state of every worker in the newest live sample of a run: {node: true|false|null}. Empty when nothing was measured yet. */
    function liveNodes(run) {
        const live = run && run.monitor && run.monitor.live;
        const samples = (live && live.timeline && live.timeline.samples) || [];
        for (let i = samples.length - 1; i >= 0; i -= 1) if (samples[i].nodes) return samples[i].nodes;
        return {};
    }

    /** Ready replicas against the starting number, per service, from the newest live sample: [{service, ready, before, state}]. */
    function liveServices(run) {
        const live = run && run.monitor && run.monitor.live;
        const timeline = (live && live.timeline) || {};
        const samples = timeline.samples || [];
        const baseline = timeline.baseline || {};
        let ready = null;
        for (let i = samples.length - 1; i >= 0; i -= 1) if (samples[i].ready) { ready = samples[i].ready; break; }
        return Object.keys(baseline).sort().filter((s) => baseline[s] > 0).map((service) => {
            const now = ready ? ready[service] : null;
            const state = now === null || now === undefined ? "unknown" : now === 0 ? "down" : now < baseline[service] ? "degraded" : "ok";
            return { service, ready: now === undefined ? null : now, before: baseline[service], state };
        });
    }

    const CHIP_TEXT = { down: "offline", degraded: "reduced", ok: "fine", unknown: "unknown", none: "not running" };

    // ---- DOM ---------------------------------------------------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [k, v] of Object.entries(attributes || {})) node.setAttribute(k, v);
        return node;
    }

    function icon(doc) {
        const span = make(doc, "span", undefined, "fv-node__icon", { "aria-hidden": "true" });
        span.innerHTML = SERVER;       // a constant of this file, never server text
        return span;
    }

    /** The row of node tiles. ``nodes``: [{name, locked}]. ``stoppedOf(name)``: true | false | null while a test runs. */
    function nodeTiles(doc, { nodes, placement, hues, picked, running, stoppedOf, onToggle, disabled, phase }) {
        const row = make(doc, "div", undefined, "fv-nodes", { role: running ? "list" : "group", "aria-label": running ? "Worker nodes" : "Pick the workers to stop" });
        const colour = hues || {};
        for (const node of nodes) {
            const chosen = picked ? picked.has(node.name) : false;
            const stopped = running ? stoppedOf(node.name) : null;
            const state = tileState(node, { picked: chosen, stopped, running, locked: node.locked, preview: !!disabled, phase });
            const interactive = !running && !node.locked && !disabled;
            const tile = make(doc, interactive ? "button" : "div", undefined, `fv-node fv-node--${state}`, interactive
                ? { type: "button", "aria-pressed": String(chosen), "data-node": node.name } : { "data-node": node.name, ...(running ? { role: "listitem" } : {}) });
            if (interactive) tile.addEventListener("click", () => onToggle(node.name));
            const head = make(doc, "span", undefined, "fv-node__head");
            head.append(icon(doc), make(doc, "strong", node.name, "fv-node__name"));
            const pods = make(doc, "span", undefined, "fv-pods");
            const here = node.locked ? [] : podsOf(placement, node.name);
            for (const group of here) {
                for (let i = 0; i < group.count; i += 1) {
                    pods.append(make(doc, "span", undefined, `fv-pod${state === "down" || state === "held" || state === "picked" ? " fv-pod--lost" : ""}`,
                        { title: group.service, style: `background:hsl(${colour[group.service] === undefined ? 215 : colour[group.service]} 62% 50%)` }));
                }
            }
            if (!here.length) pods.append(make(doc, "span", node.locked ? "control plane" : "no ready pods", "fv-pods__none"));
            tile.append(head, pods, make(doc, "span", STATE_TEXT[state], `fv-node__state fv-node__state--${state}`));
            row.append(tile);
        }
        return row;
    }

    /** Service chips: a colour AND a word, so nothing depends on colour alone. ``rows``: [{service, state, text?}]. */
    function serviceChips(doc, rows) {
        const list = make(doc, "ul", undefined, "fv-chips");
        for (const r of rows) {
            const chip = make(doc, "li", undefined, `fv-chip fv-chip--${r.state}`);
            chip.append(make(doc, "strong", r.service), make(doc, "span", r.text || CHIP_TEXT[r.state] || r.state));
            list.append(chip);
        }
        return list;
    }

    /** A legend of the service colours used by the pod squares. */
    function legend(doc, hues) {
        const list = make(doc, "ul", undefined, "fv-legend", { "aria-label": "What each square is" });
        Object.keys(hues || {}).sort().forEach((service) => {
            const li = make(doc, "li");
            li.append(make(doc, "span", undefined, "fv-pod", { style: `background:hsl(${hues[service]} 62% 50%)` }), make(doc, "span", service));
            list.append(li);
        });
        return list;
    }

    global.ResilienceFailVisual = { runPhase, awaitingCluster, tileState, podsOf, liveNodes, liveServices, nodeTiles, serviceChips, legend };
})(typeof window !== "undefined" ? window : globalThis);
