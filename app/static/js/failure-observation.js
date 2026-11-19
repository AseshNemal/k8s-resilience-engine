/*
 * Watch a real failure, for the Failure impact page. Owned by: Member 4 (validation), drawn beside Member 3's prediction.
 *
 * The developer stops a worker themselves, on a cluster they chose. This panel never stops anything: it asks the backend to record
 * the model's prediction for every worker FIRST, then reads what the cluster does, and shows the two apart. Predicted, observed and
 * "not observed" are separate columns, so a service that was predicted down and stayed ready is shown as exactly that.
 * Workflow outcomes are never observed here and are never drawn as "survived". Everything from the server is written with textContent.
 */
(function (global) {
    "use strict";

    const BASE = "/api/recommendations/failure-observations";
    const POLL_MS = 5000;
    const NAME_KEY = "resilience.audit.name";
    const list = (value) => (Array.isArray(value) ? value : []);
    const join = (names) => (list(names).length ? list(names).join(", ") : "none");

    /** The observation as plain sentences and rows. A pure function of the server's record. */
    function describe(observation) {
        if (!observation) {
            return { state: "idle", headline: "No failure is being watched.", tone: "muted", rows: [], expectations: [], timeline: [], canStart: true, canStop: false, notes: [] };
        }
        const c = observation.comparison || {};
        const active = !!observation.active;
        const watched = list(observation.watched);
        let headline;
        let tone = "muted";
        switch (observation.status) {
            case "WATCHING":
                headline = `Watching ${watched.length} worker${watched.length === 1 ? "" : "s"} (${join(watched)}). Nothing has failed yet.`; break;
            case "NODE_DOWN":
                headline = `${observation.failedNode} is not Ready. Recording which services lose their replicas.`; tone = "warn"; break;
            case "RECOVERED":
                headline = `${observation.failedNode} is Ready again. ${c.state === "MATCHED" ? "The services that went down are the ones predicted." : c.state === "DIFFERENT" ? "What went down differs from the prediction." : c.message || ""}`.trim();
                tone = c.state === "MATCHED" ? "ok" : c.state === "DIFFERENT" ? "warn" : "muted"; break;
            case "EXPIRED":
                headline = "This watch ended without a failure being completed."; break;
            case "STOPPED":
                headline = observation.failedNode ? `Stopped while ${observation.failedNode} had failed.` : "Stopped. No failure was seen."; break;
            default:
                headline = String(observation.status || "");
        }
        const rows = [];
        if (observation.failedNode && c.state && !["NO_FAILURE_SEEN", "NOT_ASSESSED", "NOT_COMPARABLE"].includes(c.state)) {
            const services = new Set([...list(c.predictedServicesDown), ...list(c.observedServicesDown),
                                      ...list(c.predictedServicesDegraded), ...list(c.observedServicesDegraded)]);
            for (const service of [...services].sort()) {
                const predicted = list(c.predictedServicesDown).includes(service) ? "down" : list(c.predictedServicesDegraded).includes(service) ? "degraded" : "stays up";
                const observed = list(c.observedServicesDown).includes(service) ? "down" : list(c.observedServicesDegraded).includes(service) ? "degraded" : "stays up";
                rows.push({ service, predicted, observed, agree: predicted === observed });
            }
        }
        // Before anything fails: what the model said for each worker, so the person knows what to expect when they stop one.
        const expectations = [];
        if (observation.status === "WATCHING") {
            for (const node of watched) {
                const p = (observation.predicted || {})[node];
                if (!p) continue;
                const parts = [];
                if (list(p.servicesDown).length) parts.push(`${list(p.servicesDown).join(", ")} would lose every ready replica`);
                if (list(p.servicesDegraded).length) parts.push(`${list(p.servicesDegraded).join(", ")} would lose some`);
                expectations.push({ node, text: parts.length ? parts.join("; ") : "no service would lose a ready replica" });
            }
        }
        const notes = [];
        if (active && observation.clusterMatch === "other") {
            headline = "The connected cluster is not the one this watch started on, so nothing is being recorded. Reconnect that cluster, or stop the watch.";
            tone = "warn";
        }
        if (c.state === "NOT_COMPARABLE" || c.state === "NOT_ASSESSED") notes.push(c.message);
        if (c.workflows && c.workflows.message && observation.failedNode) notes.push(c.workflows.message);
        const reads = observation.reads || {};
        if (active && reads.failed && reads.lastError) notes.push(`The cluster could not be read on ${reads.failed} check${reads.failed === 1 ? "" : "s"} (last: ${reads.lastError}). Those moments are unknown, not healthy.`);
        return { monitor: monitorModel(observation), requestsOn: !!(observation.monitor && observation.monitor.requests), record: { clusterUid: (observation.cluster || {}).clusterUid || null, at: observation.endedAt || observation.startedAt || null }, active, state: observation.status, headline, tone, rows, expectations, timeline: list(observation.timeline).slice(-8).reverse(), canStart: !active, canStop: active, notes, limits: list(observation.limits) };
    }

    /**
     * The measured timeline of a watch as the failure monitor takes it. While the watch is active it is the live failure; afterwards it
     * is one finished failure. Null while nothing was measured. A pure function of the server's record.
     */
    function monitorModel(observation) {
        const m = observation && observation.monitor;
        if (!m || !m.timeline || !(m.timeline.samples || []).length) return null;
        const worker = observation.failedNode || null;
        if (observation.active) return { live: { worker, kind: "watch", stage: observation.status, timeline: m.timeline, summary: m.summary }, episodes: [], comparisons: [] };
        return { live: null, comparisons: [], episodes: [{ kind: "watch", candidateId: null, worker: worker || "no worker", repeat: 1, status: observation.status, summary: m.summary, marks: m.timeline.marks, timeline: m.timeline, predictionCheck: null }] };
    }

    /** The moment in the person's own time zone (the server stores UTC). */
    function clock(iso) {
        const when = new Date(iso);
        return Number.isNaN(when.getTime()) ? "" : when.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    }

    function el(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [key, value] of Object.entries(attributes || {})) node.setAttribute(key, value);
        return node;
    }

    function draw(doc, root, model, handlers, busy, message, view) {
        const parts = [];
        const focused = doc.activeElement && root.contains && root.contains(doc.activeElement) ? doc.activeElement.getAttribute("data-role") : null;
        // A watch that has ended, or one whose cluster is not the one connected now, is a saved record: say so, with when and where from.
        const cluster = typeof window !== "undefined" ? window.ResilienceCluster : null;
        if (cluster && model.record && model.record.at && (!model.active || !cluster.isLive())) {
            parts.push(cluster.savedChip(doc, cluster.current(), model.record));
        }
        parts.push(el(doc, "p", model.headline, `fo-headline fo-headline--${model.tone}`, { role: "status" }));
        if (model.expectations && model.expectations.length) {
            parts.push(el(doc, "p", "What the model predicts for each worker, recorded when you started:", "fo-note"));
            const ul = el(doc, "ul", null, "fo-expect");
            for (const row of model.expectations) ul.append(el(doc, "li", `If ${row.node} stops: ${row.text}.`));
            parts.push(ul);
        }
        if (message) parts.push(el(doc, "p", message, "fo-message", { role: "alert" }));
        if (model.rows.length) {
            const table = el(doc, "table", null, "fo-table");
            const head = el(doc, "tr");
            for (const label of ["Service", "Predicted", "Observed"]) head.append(el(doc, "th", label));
            table.append(head);
            for (const row of model.rows) {
                const tr = el(doc, "tr", null, row.agree ? "fo-row" : "fo-row fo-row--differs");
                tr.append(el(doc, "td", row.service), el(doc, "td", row.predicted), el(doc, "td", row.observed + (row.agree ? "" : " (differs)")));
                table.append(tr);
            }
            parts.push(table);
        }
        for (const note of model.notes) if (note) parts.push(el(doc, "p", note, "fo-note"));
        const M = global.ResilienceFailureMonitor;
        if (M && model.monitor) {
            const holder = el(doc, "div", null, "fo-monitor", { "data-role": "fo-monitor" });
            const picks = M.choices(model.monitor);
            const chosen = picks.find((p) => p.key === (view && view.pick)) || picks[0];
            M.render(doc, holder, model.monitor, { selected: chosen && chosen.key, running: model.active, onSelect: (key) => view && view.select && view.select(key),
                timelineFor: (episode) => episode.timeline || null });
            parts.push(holder);
        }
        if (model.timeline.length) {
            const ul = el(doc, "ul", null, "fo-timeline");
            for (const entry of model.timeline) ul.append(el(doc, "li", `${clock(entry.at)}  ${entry.detail || entry.kind}`));
            parts.push(ul);
        }
        if (model.canStart) {
            const field = el(doc, "label", null, "fo-field");
            field.append(el(doc, "span", "Page address (optional)", "fo-field__label"));
            const input = el(doc, "input", null, "fo-field__input", { type: "url", "data-role": "fo-request-url", autocomplete: "off", placeholder: "http://localhost:8080/productpage",
                "aria-describedby": "fo-request-hint" });
            input.value = (view && view.requestUrl) || "";
            input.addEventListener("input", () => { if (view && view.setRequestUrl) view.setRequestUrl(input.value); });
            field.append(input, el(doc, "span", "Sends one real request to this page on every check (about every 5 seconds) so you can see what a user would have felt while the worker was down. Only an address on this computer is allowed. Leave it empty to skip.", "fo-note", { id: "fo-request-hint" }));
            parts.push(field);
        }
        const actions = el(doc, "div", null, "fo-actions");
        if (model.canStart) {
            const start = el(doc, "button", busy ? "Starting…" : "Start watching", "secondary-button", { type: "button", "data-role": "fo-start" });
            start.disabled = !!busy;
            start.addEventListener("click", handlers.start);
            actions.append(start);
        }
        if (model.canStop) {
            const stop = el(doc, "button", "Stop watching", "secondary-button", { type: "button", "data-role": "fo-stop" });
            stop.disabled = !!busy;
            stop.addEventListener("click", handlers.stop);
            actions.append(stop);
        }
        parts.push(actions);
        if (model.limits && model.limits.length) {
            const details = el(doc, "details", null, "fo-limits");
            details.append(el(doc, "summary", "What this does and does not show"));
            for (const text of model.limits) details.append(el(doc, "p", text, "fo-note"));
            parts.push(details);
        }
        root.replaceChildren(...parts);
        // The buttons were rebuilt: put keyboard focus back on the one that replaces the one it was on.
        if (focused && root.querySelector) {
            const again = root.querySelector(`[data-role="${focused}"]`) || root.querySelector('[data-role="fo-stop"]') || root.querySelector('[data-role="fo-start"]');
            if (again && again.focus) again.focus();
        }
    }

    /** Mounts the panel into `root`. `fetchImpl` and `timers` are injectable so the logic runs without a browser. */
    function createPanel({ root, document: doc = global.document, fetchImpl = (...a) => global.fetch(...a), timers = { set: (fn, ms) => global.setTimeout(fn, ms), clear: (id) => global.clearTimeout(id) },
                          namespace = () => null, operator = () => { try { return global.localStorage.getItem(NAME_KEY) || ""; } catch (_e) { return ""; } } }) {
        let observation = null, busy = false, message = null, timer = null, destroyed = false, loaded = false, drawn = null;
        const view = { pick: null, requestUrl: "", select: (key) => { view.pick = key; drawn = null; render(); }, setRequestUrl: (value) => { view.requestUrl = value; } };

        async function call(url, init) {
            const response = await fetchImpl(url, init);
            let body = null;
            try { body = await response.json(); } catch (_e) { body = null; }
            if (!response.ok) {
                const detail = body && body.detail;
                throw new Error((detail && detail.message) || (typeof detail === "string" ? detail : `The backend answered HTTP ${response.status}.`));
            }
            return body;
        }

        function render() {
            if (destroyed) return;
            const model = loaded ? describe(observation) : { state: "loading", headline: "Checking for a watch in progress…", tone: "muted", rows: [], expectations: [], timeline: [], canStart: false, canStop: false, notes: [] };
            // A poll that found nothing new draws nothing: the page does not flicker, and a screen reader is not read the same status again.
            const signature = JSON.stringify([model, busy, message, view.pick]);
            if (signature === drawn) return;
            drawn = signature;
            draw(doc, root, model, { start, stop }, busy, message, view);
        }

        function schedule() {
            if (timer !== null) { timers.clear(timer); timer = null; }
            if (!destroyed && observation && observation.active) timer = timers.set(refresh, POLL_MS);
        }

        async function refresh() {
            timer = null;
            try {
                const body = await call(`${BASE}/current`, { headers: { Accept: "application/json" } });
                observation = body.observation || null;
                message = null;
                loaded = true;
            } catch (error) {
                loaded = true;
                message = `The observation could not be read just now: ${error.message}`;
            }
            render(); schedule();
        }

        async function start() {
            if (busy) return;
            busy = true; message = null; render();
            try {
                const who = operator();
                observation = await call(BASE, { method: "POST", headers: { "Content-Type": "application/json", ...(who ? { "X-Operator-Name": encodeURIComponent(who) } : {}) },
                    body: JSON.stringify({ namespace: namespace(), requestUrl: view.requestUrl.trim() || null }) });
            } catch (error) {
                message = error.message;
            }
            busy = false; render(); schedule();
        }

        async function stop() {
            if (busy || !observation) return;
            busy = true; message = null; render();
            try { observation = await call(`${BASE}/${encodeURIComponent(observation.observationId)}/stop`, { method: "POST" }); }
            catch (error) { message = error.message; }
            busy = false; render(); schedule();
        }

        render();
        return { refresh, start, stop, state: () => ({ observation, busy, message }), destroy: () => { destroyed = true; if (timer !== null) timers.clear(timer); } };
    }

    global.ResilienceFailureObservation = { describe, monitorModel, createPanel, POLL_MS };

    // On the page: mount into the card, using the application chosen above it. Nothing starts until the person presses Start.
    if (global.document && typeof global.document.querySelector === "function") {
        const mount = () => {
            const root = global.document.querySelector('[data-role="observation-panel"]');
            if (!root || root.getAttribute("data-mounted")) return;
            root.setAttribute("data-mounted", "true");
            const select = global.document.querySelector('[data-role="namespace-select"]');
            createPanel({ root, namespace: () => (select && !select.disabled && select.value && !/…|Reading/.test(select.value) ? select.value : null) }).refresh();
        };
        if (global.document.readyState === "loading") global.document.addEventListener("DOMContentLoaded", mount); else mount();
    }
})(typeof window !== "undefined" ? window : globalThis);
