/*
 * Applications in the connected cluster (extension-only).
 *
 * Reads Member 2's GET /api/deployments/discovery/all and shows what it found: the namespaces that hold
 * workloads and the services in each. The Connect page lists them; the Failure impact and Recommendations pages
 * get them as suggestions for their own namespace box, so a namespace is chosen, not typed from memory.
 *
 * This file only READS and only DISPLAYS. It does not resolve topology (Member 2 does), build or guess a workflow
 * (Member 1 does), or invent scheduling facts: whatever the API did not report is shown as not reported, and a
 * namespace whose evidence is incomplete is marked so, with the reasons Member 2 gave. Cluster and namespace
 * names come from the cluster, so they are untrusted: everything is written with textContent, never as HTML.
 *
 * It reads once a minute while the tab is visible (not at all while hidden), again as soon as the connection
 * changes, and backs off after a failure. A failure replaces the list at once, so names from a cluster that is
 * no longer answering are never offered as if they were current.
 */
(function () {
    "use strict";

    const DISCOVERY_URL = "/api/deployments/discovery/all";
    const REFRESH_MS = 60000;
    const STALE_MS = 15000;
    const BACKOFF_MS = [15000, 30000, 60000];
    const REQUEST_TIMEOUT_MS = 45000;
    const TIMED_OUT = { timedOut: true };

    // Fixed wording per coded failure. The server's own message is never shown.
    const FAILURES = {
        CLUSTER_NOT_CONNECTED: { state: "not-connected", message: "No cluster is connected, so no applications are listed. Connect one on the Connect page." },
        CLUSTER_CONNECTION_FAILED: { state: "connection-failed", message: "The cluster did not answer. Check that it is running; this keeps trying." },
        CLUSTER_AUTH_FAILED: { state: "access-denied", message: "The cluster rejected the extension's credential. It may have expired or been revoked; the extension renews it by itself when it can, or reconnect on the Connect page." },
        CLUSTER_FORBIDDEN: { state: "forbidden", message: "The extension's credential cannot read what discovery needs (nodes, pods, services, deployments and replica sets)." },
        DISCOVERY_FAILED: { state: "discovery-failed", message: "The cluster returned an unexpected error during discovery." },
    };

    function describeFailure(status, body) {
        if (!body) return { state: "backend-unavailable", message: "The extension backend did not answer. This keeps trying." };
        const code = body.detail && typeof body.detail.code === "string" ? body.detail.code : "";
        if (FAILURES[code]) return FAILURES[code];
        if (/^[A-Z0-9_]{1,64}$/.test(code)) return { state: "error", message: `Discovery failed (${code}).` };
        if (status >= 200 && status < 300) {
            return { state: "unexpected-response", message: "The discovery answer was not in the expected form, so nothing is listed." };
        }
        return { state: "error", message: `Discovery failed (HTTP ${Number(status) || 0}).` };
    }

    const whole = (value) => (Number.isInteger(value) ? value : null);

    // Member 2's answer, reduced to what is shown. Throws if it is not the expected shape: a malformed answer
    // is a failure, never an empty cluster.
    function summarize(body) {
        if (!body || typeof body !== "object" || !Array.isArray(body.namespaces)) throw new Error("unexpected discovery response");
        const rows = body.namespaces.map((entry) => {
            if (!entry || typeof entry.namespace !== "string") throw new Error("unexpected discovery response");
            const listed = entry.graph && Array.isArray(entry.graph.services) ? entry.graph.services : [];
            const services = listed
                .filter((svc) => svc && typeof svc.name === "string")
                .map((svc) => ({ name: svc.name, ready: whole(svc.readyReplicas), declared: whole(svc.declaredReplicas) }))
                .sort((a, b) => a.name.localeCompare(b.name));
            const unresolved = Array.isArray(entry.unresolved) ? entry.unresolved : [];
            const reasons = new Set(unresolved.map((item) => item && item.reason).filter((reason) => typeof reason === "string"));
            let evidenceIncomplete = false;
            const evidence = entry.serviceEvidence && typeof entry.serviceEvidence === "object" ? entry.serviceEvidence : {};
            for (const item of Object.values(evidence)) {
                if (item && item.complete === false) {
                    evidenceIncomplete = true;
                    (Array.isArray(item.reasons) ? item.reasons : []).forEach((reason) => { if (typeof reason === "string") reasons.add(reason); });
                }
            }
            return {
                namespace: entry.namespace,
                services,
                serviceCount: services.length,
                status: entry.status || null,
                declaredState: entry.declaredState || null,
                reasons: Array.from(reasons).sort(),
                incomplete: entry.status === "partial" || unresolved.length > 0 || evidenceIncomplete,
            };
        });
        rows.sort((a, b) => b.serviceCount - a.serviceCount || a.namespace.localeCompare(b.namespace));
        return {
            rows,
            excluded: Array.isArray(body.excludedNamespaces) ? body.excludedNamespaces.filter((name) => typeof name === "string") : [],
            capturedAt: typeof body.capturedAt === "string" ? body.capturedAt : null,
            incomplete: rows.filter((row) => row.incomplete).length,
        };
    }

    const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;

    function optionLabel(row) {
        const size = row.serviceCount === 0 ? "no services resolved" : plural(row.serviceCount, "service");
        return `${row.namespace} · ${size}${row.incomplete ? " · incomplete evidence" : ""}`;
    }

    function options(summary) {
        return summary.rows.map((row) => ({ value: row.namespace, label: optionLabel(row) }));
    }

    function statusText(summary) {
        const hidden = summary.excluded.length ? ` System namespaces are hidden: ${summary.excluded.join(", ")}.` : "";
        if (summary.rows.length === 0) {
            return { kind: "empty", text: `Connected. No namespace holds workloads yet.${hidden}` };
        }
        const services = summary.rows.reduce((total, row) => total + row.serviceCount, 0);
        let text = `${plural(summary.rows.length, "namespace")} with applications, ${plural(services, "service")}.${hidden}`;
        if (summary.incomplete === 0) return { kind: "ok", text };
        text += ` Evidence is incomplete for ${plural(summary.incomplete, "namespace")}: some placement could not be resolved, so analyses that depend on it are refused, not estimated.`;
        return { kind: "incomplete", text };
    }

    // env: { fetchJson(url) -> {status, body}, now(), isVisible(), setTimer(fn, ms), clearTimer(id),
    //        onUpdate(snapshot), sleep(ms) (optional watchdog), requestTimeoutMs (optional) }
    function createRefresher(env) {
        let current = { state: "idle", summary: null, failure: null, updatedAt: 0 };
        let timer = null;
        let inFlight = null;
        let failures = 0;
        let lastAt = 0;

        function snapshot() { return current; }
        function publish(next) { current = next; if (env.onUpdate) env.onUpdate(current); }

        function schedule(delay) {
            if (timer !== null) env.clearTimer(timer);
            timer = env.setTimer(tick, delay);
        }

        async function read() {
            let answer;
            try {
                const work = env.fetchJson(DISCOVERY_URL);
                answer = env.sleep ? await Promise.race([work, env.sleep(env.requestTimeoutMs || REQUEST_TIMEOUT_MS).then(() => TIMED_OUT)]) : await work;
            } catch (_error) {
                answer = null;
            }
            return answer === TIMED_OUT ? null : answer;
        }

        async function run() {
            if (current.state === "idle") publish({ ...current, state: "loading" });
            const answer = await read();
            lastAt = env.now();
            let next;
            if (answer && answer.status >= 200 && answer.status < 300) {
                try {
                    next = { state: "ok", summary: summarize(answer.body), failure: null, updatedAt: lastAt };
                } catch (_error) {
                    next = { state: "error", summary: null, failure: describeFailure(200, answer.body), updatedAt: lastAt };
                }
            } else {
                next = { state: "error", summary: null, failure: describeFailure(answer ? answer.status : 0, answer ? answer.body : null), updatedAt: lastAt };
            }
            failures = next.state === "ok" ? 0 : failures + 1;
            publish(next);
            schedule(failures ? BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)] : REFRESH_MS);
            return snapshot();
        }

        // Single flight: a timer, a tab coming back and a connection change can coincide.
        function refresh() {
            if (!inFlight) inFlight = run().finally(() => { inFlight = null; });
            return inFlight;
        }

        function refreshIfStale() {
            return env.now() - lastAt >= STALE_MS ? refresh() : Promise.resolve(snapshot());
        }

        // The connection says it changed (it also says so on every routine check). After a failure the answer may
        // be different now, so read at once; while the list is good, only when it has gone stale.
        function connectionChanged() {
            return current.state === "error" ? refresh() : refreshIfStale();
        }

        // A hidden tab reads nothing, but keeps watching so it resumes when shown.
        async function tick() {
            timer = null;
            if (!env.isVisible()) {
                schedule(REFRESH_MS);
                return snapshot();
            }
            return refresh();
        }

        function stop() {
            if (timer !== null) env.clearTimer(timer);
            timer = null;
        }

        return { start: refresh, refresh, refreshIfStale, connectionChanged, stop, snapshot };
    }

    // What the list under a namespace box offers. It is a list of its own, not the browser's <datalist>: that
    // popup is drawn by the browser, which inside Docker Desktop placed it away from the box and repeated the name,
    // and it hides every namespace except the one already typed. Here the list is always under the box and shows
    // every namespace the cluster reported. While the box holds text that is not (yet) a namespace, it narrows to
    // the namespaces containing that text; an exact namespace in the box means "this one is chosen", so all show.
    function suggestions(summary, typed) {
        const all = summary.rows.map((row) => ({
            value: row.namespace,
            detail: `${row.serviceCount === 0 ? "no services resolved" : plural(row.serviceCount, "service")}${row.incomplete ? " · incomplete evidence" : ""}`,
        }));
        const text = String(typed || "").trim().toLowerCase();
        if (!text || all.some((entry) => entry.value.toLowerCase() === text)) return all;
        return all.filter((entry) => entry.value.toLowerCase().includes(text));
    }

    // The namespace a blank or empty "default" box should take: the one with the most resolved services
    // (same rule as Deployment Topology). Returns null when the typed value already names a real
    // application namespace, or when nothing in the cluster has services yet.
    function preferredNamespace(summary, typed) {
        const text = String(typed || "").trim();
        const rows = summary && Array.isArray(summary.rows) ? summary.rows : [];
        const withServices = rows.filter((row) => row.serviceCount > 0);
        if (withServices.length === 0) return null;
        const exact = rows.find((row) => row.namespace === text);
        if (exact && exact.serviceCount > 0) return null;
        if (text && text !== "default") return null;
        let best = withServices[0];
        for (const row of withServices) {
            if (row.serviceCount > best.serviceCount) best = row;
        }
        return best.namespace;
    }

    // The next highlighted row for an arrow key: it stops at the ends instead of wrapping, so a held key is predictable.
    function moveActive(current, count, key) {
        if (count <= 0) return -1;
        if (key === "ArrowDown") return current < 0 ? 0 : Math.min(current + 1, count - 1);
        if (key === "ArrowUp") return current < 0 ? count - 1 : Math.max(current - 1, 0);
        return current;
    }

    window.__resilienceNamespaces = { DISCOVERY_URL, REFRESH_MS, summarize, optionLabel, options, statusText, describeFailure, createRefresher, suggestions, preferredNamespace, moveActive };
    if (!window.document || !window.document.addEventListener) return;

    // ---- browser wiring: only on a page that has something to fill in; every other page never reads the cluster ----
    const appsList = document.querySelector('[data-role="apps-list"]');
    const appsCard = document.querySelector('[data-role="apps-card"]');
    const appsStatus = document.querySelector('[data-role="apps-status"]');
    const inputs = Array.from(document.querySelectorAll('[data-role="namespace"]'));
    if (!appsList && inputs.length === 0) return;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    let lastSummary = null;

    // One suggestion list per namespace box, directly under it. It follows the same rules for every page that has a
    // box: choosing a row fills the box and announces the "change" the page already listens for, so the page reloads
    // at once; typing still reloads when the box loses focus, as before.
    function createCombo(input, index) {
        const list = el("ul", "ns-suggest");
        list.id = `resilience-ns-${index}`;
        list.hidden = true;
        list.setAttribute("role", "listbox");
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-expanded", "false");
        input.setAttribute("aria-controls", list.id);
        input.insertAdjacentElement("afterend", list);

        let items = [];
        let active = -1;

        function close() {
            list.hidden = true;
            active = -1;
            input.setAttribute("aria-expanded", "false");
        }

        function choose(value) {
            input.value = value;
            close();
            input.dispatchEvent(new Event("change", { bubbles: true }));
        }

        function draw() {
            list.replaceChildren(...items.map((entry, position) => {
                const row = el("li", "ns-suggest__item");
                row.setAttribute("role", "option");
                row.setAttribute("aria-selected", String(position === active));
                row.dataset.current = String(entry.value === input.value);
                row.append(el("strong", "ns-suggest__name", entry.value), el("span", "ns-suggest__detail", entry.detail));
                // mousedown, not click, and the default is cancelled: the box must keep focus (blur would close the list
                // before the click arrives), and a click inside a <label> would otherwise re-focus and reopen the box.
                row.addEventListener("mousedown", (event) => { event.preventDefault(); choose(entry.value); });
                row.addEventListener("click", (event) => event.preventDefault());
                return row;
            }));
        }

        function open() {
            items = lastSummary ? suggestions(lastSummary, input.value) : [];
            if (items.length === 0) { close(); return; }
            draw();
            list.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }

        input.addEventListener("focus", open);
        input.addEventListener("click", open);
        input.addEventListener("input", () => { active = -1; open(); });
        input.addEventListener("blur", close);
        input.addEventListener("keydown", (event) => {
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                if (list.hidden) open();
                active = moveActive(active, items.length, event.key);
                draw();
            } else if (event.key === "Enter" && !list.hidden && active >= 0 && items[active]) {
                event.preventDefault();
                choose(items[active].value);
            } else if (event.key === "Escape" && !list.hidden) {
                event.preventDefault();
                close();
            }
        });

        // The cluster's answer changed: a list that is open is redrawn, or closed when nothing is offered any more.
        return { refresh() { if (!list.hidden) open(); }, close };
    }

    const hints = inputs.map((input) => {
        const hint = el("p", "namespace-hint");
        hint.setAttribute("role", "status");
        hint.dataset.role = "namespace-hint";
        input.insertAdjacentElement("afterend", hint);
        return hint;
    });
    const combos = inputs.map((input, index) => createCombo(input, index));

    function serviceLine(service) {
        const replicas = service.ready === null || service.declared === null ? "replicas not reported" : `${service.ready}/${service.declared} ready`;
        return `${service.name} · ${replicas}`;
    }

    function renderList(snap) {
        appsList.replaceChildren();
        if (snap.state !== "ok") return;
        for (const row of snap.summary.rows) {
            const item = el("li", "connect__app");
            item.dataset.incomplete = String(row.incomplete);
            item.append(el("strong", "connect__app-name", row.namespace));
            item.append(el("span", "connect__app-meta", row.serviceCount === 0 ? " · no services resolved" : ` · ${plural(row.serviceCount, "service")}`));
            if (row.incomplete) {
                const why = row.reasons.length ? ` (${row.reasons.join(", ")})` : "";
                item.append(el("span", "connect__app-warning", ` · incomplete evidence${why}`));
            }
            if (row.serviceCount) item.append(el("div", "connect__app-services", row.services.map(serviceLine).join(" · ")));
            appsList.append(item);
        }
    }

    function render(snap) {
        let kind = "loading";
        let text = "Reading the cluster…";
        if (snap.state === "ok") {
            const status = statusText(snap.summary);
            kind = status.kind;
            text = status.text;
        } else if (snap.state === "error") {
            kind = "error";
            text = snap.failure.message;
        }
        lastSummary = snap.state === "ok" ? snap.summary : null;   // a failed read removes the names at once
        // First visit: the HTML boxes start as "default". When that namespace has no applications and
        // another one does, fill the box once so Failure impact / Recommendations open on real workloads.
        if (lastSummary) {
            inputs.forEach((input) => {
                if (input.dataset.nsPreferred === "1") return;
                const next = preferredNamespace(lastSummary, input.value);
                if (!next || next === input.value) return;
                input.dataset.nsPreferred = "1";
                input.value = next;
                input.dispatchEvent(new Event("change", { bubbles: true }));
            });
        }
        combos.forEach((combo) => combo.refresh());
        hints.forEach((hint) => { hint.textContent = text; hint.dataset.kind = kind; });
        if (appsCard) appsCard.hidden = false;
        if (appsStatus) { appsStatus.textContent = text; appsStatus.dataset.kind = kind; }
        if (appsList) renderList(snap);
    }

    async function fetchJson(url) {
        try {
            const response = await fetch(url);
            let body = null;
            try { body = await response.json(); } catch (_error) { body = null; }
            return { status: response.status, body };
        } catch (_error) {
            return { status: 0, body: null };
        }
    }

    const refresher = createRefresher({
        fetchJson,
        now: () => Date.now(),
        isVisible: () => !document.hidden,
        setTimer: (fn, ms) => setTimeout(fn, ms),
        clearTimer: (id) => clearTimeout(id),
        sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
        onUpdate: render,
    });
    window.__resilienceRefresher = refresher;
    document.addEventListener("DOMContentLoaded", () => refresher.start());
    // The connection was renewed, reconnected, set up or removed: read again now if the last read failed.
    window.addEventListener("resilience-cluster-changed", () => refresher.connectionChanged());
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresher.refreshIfStale(); });
})();
