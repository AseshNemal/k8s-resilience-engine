/*
 * One answer, for every page, to "is a cluster connected and answering right now?", and the words and pictures that follow from it.
 *
 * Why this exists. The pages draw two kinds of thing: LIVE data (read from the cluster just now) and SAVED records the backend keeps
 * (past test runs, applied changes, watched failures). A saved record is legitimate evidence, but it must never look like the cluster
 * is being analysed or measured at this moment. So a page asks this module first, and labels every saved record with where it came
 * from: "Saved result", "Saved result · cluster not connected", "Saved result from a different cluster".
 *
 * The state comes from GET /api/recommendations/cluster-status (one identity read, nothing cached on the server). It is never guessed:
 * if the status cannot be read at all, the state is "backend-unreachable", not "connected". All text goes through textContent.
 */
(function () {
    "use strict";

    const ENDPOINT = "/api/recommendations/cluster-status";
    const FRESH_MS = 8000;
    const POLL_MS = 20000;
    const SVG = "http://www.w3.org/2000/svg";

    const WORDS = {
        checking: { chip: "Checking cluster…", tone: "muted", title: "Checking the cluster…", body: "" },
        connected: { chip: "Live · connected", tone: "live", title: "Connected", body: "" },
        "not-connected": {
            chip: "Offline · saved results only", tone: "offline", title: "Connect your cluster to begin",
            body: "Nothing here is live until a cluster is connected. Past test results are kept and stay available, marked as saved.",
        },
        unreachable: {
            chip: "Cluster not answering", tone: "offline", title: "Your cluster is not answering",
            body: "A cluster is set up, but it did not respond. Check that it is running (for Docker Desktop, that Kubernetes is enabled), then try again.",
        },
        "access-denied": {
            chip: "Access needs attention", tone: "offline", title: "The cluster rejected the connection",
            body: "The saved credential may have expired or been removed. Connect the cluster again to continue.",
        },
        unidentified: {
            chip: "Cluster not identified", tone: "offline", title: "The cluster answered, but could not be identified",
            body: "Without its identity the app cannot tell this cluster from another, so it will not treat anything as live.",
        },
        "backend-unreachable": {
            chip: "App not answering", tone: "offline", title: "The app's backend is not answering",
            body: "Wait a few seconds and reload. If it keeps happening, restart the extension.",
        },
    };

    let current = { state: "checking", clusterUid: null, checkedAt: null };
    let lastAt = 0;
    let inflight = null;
    const listeners = new Set();
    let timer = null;

    // ---- pure helpers (exported for tests) ----------------------------------------------------------------------------------------

    function words(state) {
        return WORDS[state] || WORDS["backend-unreachable"];
    }

    function isLive(status) {
        return !!status && status.state === "connected";
    }

    function ago(iso, now) {
        const then = iso ? new Date(iso).getTime() : NaN;
        if (!Number.isFinite(then)) return null;
        const seconds = Math.max(0, Math.round(((now === undefined ? Date.now() : now) - then) / 1000));
        if (seconds < 90) return "just now";
        const minutes = Math.round(seconds / 60);
        if (minutes < 90) return `${minutes} min ago`;
        const hours = Math.round(minutes / 60);
        if (hours < 48) return `${hours} h ago`;
        return `${Math.round(hours / 24)} days ago`;
    }

    /**
     * How to describe a record the backend SAVED (a run, a change, a watch), given the cluster connected now.
     * ``record``: {clusterUid, at, clusterName}. Returns {kind, tone, label, detail}. Never "live".
     */
    function describeSaved(status, record, now) {
        const when = ago(record && record.at, now);
        const stamp = when ? ` · ${when}` : "";
        const name = record && record.clusterName ? ` · ${record.clusterName}` : "";
        if (!isLive(status)) {
            return { kind: "saved-offline", tone: "offline", label: `Saved result${stamp}`,
                detail: `Recorded earlier${name}. The cluster is not connected, so this is not live and nothing is being measured now.` };
        }
        if (record && record.clusterUid && status.clusterUid && record.clusterUid !== status.clusterUid) {
            return { kind: "saved-other", tone: "other", label: `Saved result from a different cluster${stamp}`,
                detail: `Recorded on another cluster${name}. It says nothing about the cluster connected now.` };
        }
        if (record && record.clusterUid && status.clusterUid) {
            return { kind: "saved-same", tone: "saved", label: `Saved result${stamp}`,
                detail: "Recorded on this cluster earlier. The cluster may have changed since; run it again for current behaviour." };
        }
        return { kind: "saved-unknown", tone: "saved", label: `Saved result${stamp}`,
            detail: "Recorded earlier; which cluster it came from was not saved with it." };
    }

    function connectHref() {
        // Only the Docker Desktop extension has a Connect page; the standalone app has no such page, so no link is invented.
        if (typeof window !== "undefined" && window.__resilienceChrome) return "connect.html" + (window.location.search || "");
        return null;
    }

    // ---- the shared state -------------------------------------------------------------------------------------------------------

    function publish(next) {
        const changed = next.state !== current.state || next.clusterUid !== current.clusterUid;
        current = next;
        if (typeof document !== "undefined" && document.body) document.body.dataset.cluster = next.state;
        paintChip();
        if (changed) listeners.forEach((fn) => { try { fn(current); } catch (_e) { /* a listener must not break the others */ } });
    }

    async function refresh(force) {
        if (!force && inflight) return inflight;
        if (!force && Date.now() - lastAt < FRESH_MS && current.state !== "checking") return current;
        inflight = (async () => {
            try {
                const response = await fetch(ENDPOINT, { headers: { Accept: "application/json" } });
                if (!response.ok) throw new Error(String(response.status));
                const body = await response.json();
                publish({ ...body, state: typeof body.state === "string" ? body.state : "backend-unreachable" });
            } catch (_error) {
                publish({ state: "backend-unreachable", clusterUid: null, checkedAt: new Date().toISOString() });
            } finally {
                lastAt = Date.now();
                inflight = null;
            }
            return current;
        })();
        return inflight;
    }

    function onChange(fn) {
        listeners.add(fn);
        return () => listeners.delete(fn);
    }

    // ---- the header chip ----------------------------------------------------------------------------------------------------------

    function paintChip() {
        if (typeof document === "undefined") return;
        const chip = document.querySelector('[data-role="evidence-chip"]');
        if (!chip || chip.dataset.custom === "true") return;
        const w = words(current.state);
        chip.className = `pill-badge pill-badge--${w.tone === "live" ? "teal" : w.tone === "offline" ? "amber" : "muted"} cs-chip cs-chip--${w.tone}`;
        const label = current.state === "connected" && current.context ? `Live · ${current.context}` : w.chip;
        chip.textContent = label;
        chip.title = current.state === "connected"
            ? "A cluster is connected and answering. Live analysis and measurements come from it."
            : "Nothing on this page is live. Saved results from earlier sessions stay available and are marked as saved.";
    }

    // ---- DOM ----------------------------------------------------------------------------------------------------------------------

    function make(doc, tag, text, className, attributes) {
        const node = doc.createElement(tag);
        if (text !== undefined && text !== null) node.textContent = String(text);
        if (className) node.className = className;
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
        return node;
    }

    function svg(doc, tag, attributes) {
        const node = doc.createElementNS ? doc.createElementNS(SVG, tag) : doc.createElement(tag);
        for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, String(value));
        return node;
    }

    /** A small picture of three nodes with no pods in them and a cable that does not reach: "nothing connected yet". */
    function illustration(doc) {
        const art = svg(doc, "svg", { viewBox: "0 0 220 96", class: "cs-art", role: "img", "aria-label": "Three empty nodes and an unplugged cable" });
        [0, 1, 2].forEach((i) => {
            const x = 14 + i * 66;
            art.append(svg(doc, "rect", { x, y: 30, width: 52, height: 44, rx: 8, class: "cs-art__node" }));
            [0, 1].forEach((j) => art.append(svg(doc, "rect", { x: x + 9 + j * 18, y: 44, width: 14, height: 14, rx: 3, class: "cs-art__pod" })));
        });
        art.append(svg(doc, "path", { d: "M110 30 V14 H196", class: "cs-art__cable", fill: "none" }));
        art.append(svg(doc, "circle", { cx: 204, cy: 14, r: 6, class: "cs-art__plug" }));
        return art;
    }

    /**
     * The friendly "not connected" panel: a picture, one sentence, what still works, and the next action. Replaces the raw error card.
     * ``options``: onRetry, whatStillWorks (string|null), details (a code or technical line, folded away).
     */
    function renderPanel(container, status, options) {
        if (!container) return null;
        const opts = options || {};
        const doc = opts.document || (typeof document !== "undefined" ? document : null);
        const w = words(status && status.state);
        const card = make(doc, "section", undefined, `cs-panel cs-panel--${w.tone}`, { role: "status", "data-state": (status && status.state) || "unknown", "data-role": "cluster-panel-offline" });
        card.append(illustration(doc));
        const text = make(doc, "div", undefined, "cs-panel__text");
        text.append(make(doc, "h3", opts.title || w.title, "cs-panel__title"), make(doc, "p", opts.message || w.body, "cs-panel__body"));
        const actions = make(doc, "div", undefined, "cs-panel__actions");
        const href = connectHref();
        if (href && status && status.state !== "backend-unreachable") {
            actions.append(make(doc, "a", status.state === "not-connected" ? "Connect a cluster" : "Reconnect the cluster", "primary-button", { href, "data-role": "cluster-connect-link" }));
        }
        if (opts.onRetry) {
            const retry = make(doc, "button", "Check again", "secondary-button", { type: "button", "data-role": "retry-button" });
            retry.addEventListener("click", opts.onRetry);
            actions.append(retry);
        }
        text.append(actions);
        const steps = [];
        if (status && status.state === "not-connected") {
            steps.push("Start your cluster (Docker Desktop: Settings → Kubernetes → Enable, or a kind cluster).");
            steps.push(href ? "Open Connect cluster and approve it once." : "Point KUBECONFIG at your cluster, then restart the backend.");
            steps.push("Come back here: the page checks again by itself.");
        }
        if (steps.length) {
            const list = make(doc, "ol", undefined, "cs-panel__steps");
            steps.forEach((line) => list.append(make(doc, "li", line)));
            text.append(list);
        }
        if (opts.whatStillWorks) text.append(make(doc, "p", opts.whatStillWorks, "cs-panel__still"));
        if (opts.details) {
            const fold = make(doc, "details", undefined, "cs-panel__details");
            fold.append(make(doc, "summary", "Technical details"), make(doc, "code", opts.details));
            text.append(fold);
        }
        card.append(text);
        container.replaceChildren(card);
        return card;
    }

    /** A one-line strip for a page that shows saved records while offline. */
    function renderBanner(container, status, options) {
        if (!container) return null;
        const doc = (options && options.document) || document;
        container.replaceChildren();
        if (isLive(status) || !status || status.state === "checking") { container.hidden = true; return null; }
        container.hidden = false;
        const w = words(status.state);
        const strip = make(doc, "div", undefined, "cs-banner", { role: "status", "data-state": status.state });
        strip.append(make(doc, "span", "●", "cs-banner__dot", { "aria-hidden": "true" }),
            make(doc, "span", `${w.title}. Anything shown below is saved from earlier — not live.`, "cs-banner__text"));
        const href = connectHref();
        if (href) strip.append(make(doc, "a", "Connect", "cs-banner__link", { href }));
        if (options && options.onRetry) {
            const retry = make(doc, "button", "Check again", "cs-banner__btn", { type: "button" });
            retry.addEventListener("click", options.onRetry);
            strip.append(retry);
        }
        container.append(strip);
        return strip;
    }

    /** The label that goes on a saved record. */
    function savedChip(doc, status, record, now) {
        const d = describeSaved(status, record, now);
        const chip = make(doc, "span", undefined, `cs-saved cs-saved--${d.tone}`, { "data-saved": d.kind, title: d.detail });
        chip.append(make(doc, "span", "⏱", "cs-saved__ico", { "aria-hidden": "true" }), make(doc, "span", d.label));
        return chip;
    }

    function start() {
        refresh(true);
        if (typeof document !== "undefined") {
            document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(true); });
            window.addEventListener("resilience-cluster-changed", () => refresh(true));
        }
        if (timer === null) timer = setInterval(() => { if (typeof document === "undefined" || !document.hidden) refresh(true); }, POLL_MS);
    }

    if (typeof window !== "undefined" && typeof document !== "undefined" && document.addEventListener) {
        if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start);
        else start();
    }

    window.ResilienceCluster = {
        refresh, onChange, current: () => current, isLive: () => isLive(current), words, describeSaved, savedChip, renderPanel, renderBanner,
        connectHref, ago, illustration, _isLive: isLive,
    };
})();
