/*
 * The "Tracing service" card on the Connect page (extension-only).
 *
 * Open the extension, turn tracing on once, and it keeps itself running: the host helper (through
 * trace-service.js) starts the trace container after a native approval dialog, and afterwards the page and the
 * helper's background job check on it and bring back that one container if it stopped. This card shows what is
 * true: whether the trace service answers, which addresses were tested, whether real traces are arriving and
 * from which services, and, for each application in the cluster, whether it is producing traces.
 *
 * What it never does: send, seed or invent a trace; change a Deployment or any other cluster object; install
 * Istio or anything else; start anything without the person's approval. For an application that sends nothing it
 * only writes guidance text for the person to apply themselves.
 *
 * Everything shown from the cluster or the trace service is untrusted and is written with textContent only.
 */
(function () {
    "use strict";

    const BACKEND = "/_extension/tracing";
    const REFRESH_MS = 15000;
    const APPS_MS = 30000;
    const ENSURE_COOLDOWN_MS = 120000;
    const MAX_NAMESPACES = 5;
    const POD_ADDRESS_FALLBACK = "host.docker.internal:4318";

    // ---- wording ------------------------------------------------------------------------------------------------

    function ago(iso, nowMs) {
        const then = Date.parse(iso);
        if (!Number.isFinite(then)) return "";
        const seconds = Math.max(0, Math.round((nowMs - then) / 1000));
        if (seconds < 60) return `${seconds} s ago`;
        if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
        if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
        return `${Math.floor(seconds / 86400)} d ago`;
    }

    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

    // The trace service's own state (from the helper), then what it has received (from the backend).
    function describe(input, nowMs) {
        const helper = input.helper || { state: "helper-unavailable", trace: null };
        const backend = input.backend || null;
        const reachable = Boolean(backend && backend.query && backend.query.reachable === true);
        const telemetry = backend && backend.telemetry ? backend.telemetry : null;

        let headline;
        if (reachable) {
            headline = helper.state === "trace-running" ? "Running (started by the extension)"
                : helper.state === "trace-running-external" ? "Running (started somewhere else; the extension leaves it as it is)"
                : "Running";
        } else if (!backend) {
            headline = "The extension backend did not answer.";
        } else {
            const bridge = window.__resilienceTrace;
            headline = bridge ? bridge.messageFor(helper.state === "trace-running" ? "trace-not-ready" : helper.state) : "Not running.";
        }

        let detail = "";
        let kind = "idle";
        if (!backend) {
            kind = "warn";
        } else if (!reachable) {
            kind = helper.state === "docker-unavailable" || helper.state === "trace-port-in-use" || helper.state === "trace-start-failed" ? "error" : "idle";
            detail = "The query service is not answering, so nothing is known about incoming traces.";
        } else if (telemetry && telemetry.state === "RECEIVING") {
            kind = "ok";
            const services = Number.isInteger(telemetry.serviceCount) ? plural(telemetry.serviceCount, "service") : "services";
            detail = `Receiving real traces from ${services}. Newest began ${ago(telemetry.newestTraceAt, nowMs)}.`;
        } else if (telemetry && telemetry.state === "STALE") {
            kind = "warn";
            const minutes = Math.round((telemetry.freshSeconds || 300) / 60);
            detail = `${plural(telemetry.serviceCount || 0, "service")} reported before, but no trace began in the last ${plural(minutes, "minute")}.`;
        } else if (telemetry && telemetry.state === "NO_SERVICES") {
            kind = "idle";
            detail = "Waiting for the first trace. Nothing has been received yet; the extension never sends a trace of its own.";
        }
        return { kind, headline, detail, reachable };
    }

    // Which buttons make sense. A trace service someone else started is never offered for enabling or stopping.
    function actions(helper) {
        if (!helper || helper.state === "helper-unavailable" || helper.state === "timed-out") {
            return { enable: false, turnOff: false, check: false };
        }
        const external = helper.state === "trace-running-external";
        const enabled = Boolean(helper.trace && helper.trace.enabled);
        const unusable = external || helper.state === "docker-unavailable";
        return {
            enable: !enabled && !unusable,
            turnOff: !external && (enabled || helper.state === "trace-running"),
            check: helper.state !== "docker-unavailable",
        };
    }

    const ENDPOINT_LABELS = {
        "query": "Query service",
        "otlp-http": "Receiver for traces over HTTP (port 4318)",
        "otlp-grpc": "Receiver for traces over gRPC (port 4317)",
        "pods-otlp-http": "From the Kubernetes nodes to the HTTP receiver",
    };

    function describeEndpoint(endpoint) {
        const label = ENDPOINT_LABELS[endpoint.name] || endpoint.name;
        const state = endpoint.reachable === true ? "ok" : endpoint.reachable === false ? "fail" : "unknown";
        let text = `${label}: ${state === "ok" ? "answers" : state === "fail" ? "does not answer" : "not tested"}`;
        if (endpoint.address) text += ` · ${endpoint.address}`;
        if (endpoint.name === "pods-otlp-http") {
            text += state === "ok" ? ". Every node reached it; a Pod itself has not been tested."
                : state === "fail" ? ". A node could not reach it, so applications in the cluster cannot send traces here yet."
                : ". Could not be tested here (no Kubernetes node containers were found, or Docker could not be asked).";
        }
        return { state, text };
    }

    function describeService(row, freshSeconds, nowMs) {
        if (row.state === "PRODUCING") return `${row.service}: producing traces (newest began ${ago(row.lastTraceAt, nowMs)})`;
        if (row.state === "STALE") {
            return `${row.service}: known to the trace service, but no trace began in the last ${plural(Math.round((freshSeconds || 300) / 60), "minute")}`;
        }
        return `${row.service}: no traces received under this name`;
    }

    function describeApplication(app, nowMs) {
        return {
            namespace: app.namespace,
            summary: `${app.namespace}: ${app.producing} of ${plural(app.total, "service")} producing traces`,
            allProducing: app.total > 0 && app.producing === app.total,
            missing: app.services.filter((row) => row.state !== "PRODUCING").map((row) => row.service),
            lines: app.services.map((row) => describeService(row, app.freshSeconds, nowMs)),
        };
    }

    // Guidance text for services that send no traces. Text only: nothing is applied, and nothing here knows how to.
    function setupGuidance(input) {
        const pods = input.pods || null;
        const address = pods && pods.address ? pods.address : POD_ADDRESS_FALLBACK;
        const names = input.services.join(", ");
        const lines = [
            `${names} in "${input.namespace}" have not sent any trace to the trace service.`,
            "An application sends traces only if it contains an OpenTelemetry SDK or agent, or runs behind a mesh sidecar that reports them. " +
            "This extension cannot add either, and it does not change your Deployments.",
        ];
        if (pods && pods.reachable === true) {
            lines.push(`The Kubernetes nodes reached ${address}. A Pod has not been tested, so check one trace arrives before relying on it.`);
        } else if (pods && pods.reachable === false) {
            lines.push(`The Kubernetes nodes could NOT reach ${address}. Traces sent there from the cluster would not arrive, so do not apply this yet. ` +
                "The trace service listens on this computer only. Publishing it more widely is a decision for you; the extension does not do it.");
        } else {
            lines.push(`Whether the cluster can reach ${address} has not been tested. Use "Check connections" first.`);
        }
        lines.push("If the application already has an OpenTelemetry SDK or agent, set these environment variables on its Deployment " +
            "(changing a Deployment restarts its Pods, so review it first):");
        const snippet = [
            `OTEL_EXPORTER_OTLP_ENDPOINT=http://${address}`,
            "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf",
            "OTEL_SERVICE_NAME=<the service's name, as it should appear in traces>",
        ].join("\n");
        lines.push("Variables only configure an SDK that is already there. Bookinfo's applications forward trace headers but export no spans " +
            "themselves, so these variables alone send nothing there. For Bookinfo see docs/TRACING_INFRASTRUCTURE.md (Bookinfo), " +
            "which explains the options and what each would change.");
        return { title: "Set up tracing", lines, snippet };
    }

    // ---- controller (no DOM) ------------------------------------------------------------------------------------

    // env: { traceService, fetchJson(url) -> {status, body}, namespaces() -> refresher snapshot or null, now(), onUpdate(view) }
    function createPanel(env) {
        const state = {
            helper: null, backend: null, endpoints: [], apps: [], busy: "", message: "",
            lastEnsureAt: -Infinity,
        };

        const available = () => Boolean(env.traceService && env.traceService.available);

        function view() {
            const nowMs = env.now();
            return {
                ...describe({ helper: state.helper, backend: state.backend }, nowMs),
                actions: actions(state.helper),
                helperAvailable: available(),
                busy: state.busy,
                message: state.message,
                endpoints: state.endpoints.map(describeEndpoint),
                apps: state.apps.map((app) => describeApplication(app, nowMs)),
                podEndpoint: state.endpoints.find((e) => e.name === "pods-otlp-http") || null,
            };
        }

        function publish() { if (env.onUpdate) env.onUpdate(view()); }

        async function backendStatus() {
            const answer = await env.fetchJson(`${BACKEND}/status`);
            return answer && answer.status === 200 && answer.body && answer.body.telemetry ? answer.body : null;
        }

        // Brings back the person's own trace service when it should be running and is not. It asks nobody: the
        // person approved this when they turned tracing on, and the helper refuses anything else.
        async function maybeEnsure() {
            const helper = state.helper;
            if (!helper || !helper.trace || !helper.trace.enabled) return;
            if (["trace-running", "trace-running-external", "docker-unavailable"].includes(helper.state)) return;
            if (env.now() - state.lastEnsureAt < ENSURE_COOLDOWN_MS) return;
            state.lastEnsureAt = env.now();
            const result = await env.traceService.ensure();
            if (result) state.helper = { ...state.helper, ...result };
        }

        async function refresh() {
            if (available()) {
                state.helper = await env.traceService.status();
                await maybeEnsure();
            } else {
                state.helper = { state: "helper-unavailable", trace: null };
            }
            state.backend = await backendStatus();
            publish();
            return view();
        }

        async function validate() {
            if (!available()) return view();
            state.busy = "validate";
            publish();
            const result = await env.traceService.validate();
            state.busy = "";
            state.endpoints = result && result.trace ? result.trace.endpoints : [];
            publish();
            return view();
        }

        async function enable() {
            if (!available()) return view();
            state.busy = "enable";
            state.message = "";
            publish();
            const result = await env.traceService.enable();
            state.busy = "";
            state.message = result ? result.message : "";
            state.helper = result || state.helper;
            await refresh();
            if (result && result.ok) await validate();
            return view();
        }

        async function turnOff() {
            if (!available()) return view();
            state.busy = "turn-off";
            publish();
            const result = await env.traceService.stop();
            state.busy = "";
            state.message = result ? result.message : "";
            state.endpoints = [];
            await refresh();
            return view();
        }

        async function applications() {
            const snapshot = env.namespaces ? env.namespaces() : null;
            const rows = snapshot && snapshot.state === "ok" && snapshot.summary ? snapshot.summary.rows : [];
            const found = [];
            for (const row of rows.slice(0, MAX_NAMESPACES)) {
                if (!row.services || row.services.length === 0) continue;
                const query = new URLSearchParams();
                query.set("namespace", row.namespace);
                row.services.forEach((svc) => query.append("service", svc.name));
                const answer = await env.fetchJson(`${BACKEND}/applications?${query.toString()}`);
                if (answer && answer.status === 200 && answer.body && Array.isArray(answer.body.services)) found.push(answer.body);
            }
            state.apps = found;
            publish();
            return view();
        }

        function guidanceFor(namespace) {
            const app = state.apps.find((entry) => entry.namespace === namespace);
            if (!app) return null;
            const missing = app.services.filter((row) => row.state !== "PRODUCING").map((row) => row.service);
            if (missing.length === 0) return null;
            return setupGuidance({ namespace, services: missing, pods: view().podEndpoint });
        }

        return { refresh, validate, enable, turnOff, applications, guidanceFor, view };
    }

    window.__resilienceTracingPanel = { ago, describe, actions, describeEndpoint, describeService, describeApplication, setupGuidance, createPanel, REFRESH_MS, APPS_MS, ENSURE_COOLDOWN_MS };
    if (!window.document || !window.document.addEventListener) return;

    // ---- browser wiring: only the Connect page has the card ----------------------------------------------------------

    const card = document.querySelector('[data-role="tracing-card"]');
    if (!card) return;
    const part = (role) => card.querySelector(`[data-role="${role}"]`);
    const nodes = {
        headline: part("tracing-headline"), detail: part("tracing-detail"), message: part("tracing-message"),
        enable: part("tracing-enable"), off: part("tracing-off"), check: part("tracing-check"),
        endpoints: part("tracing-endpoints"), apps: part("tracing-apps"),
        guidance: part("tracing-guidance"), guidanceTitle: part("tracing-guidance-title"),
        guidanceText: part("tracing-guidance-text"), guidanceSnippet: part("tracing-guidance-snippet"), copy: part("tracing-copy"),
    };

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
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

    let panel = null;

    function openGuidance(namespace) {
        const guidance = panel.guidanceFor(namespace);
        if (!guidance) return;
        nodes.guidanceTitle.textContent = `${guidance.title}: ${namespace}`;
        nodes.guidanceText.replaceChildren(...guidance.lines.map((line) => el("p", "", line)));
        nodes.guidanceSnippet.textContent = guidance.snippet;
        nodes.guidance.hidden = false;
        nodes.guidance.open = true;
    }

    function render(v) {
        card.hidden = false;
        nodes.headline.textContent = v.headline;
        nodes.headline.dataset.kind = v.kind;
        nodes.detail.textContent = v.detail;
        nodes.message.textContent = v.message;
        nodes.enable.hidden = !v.actions.enable;
        nodes.off.hidden = !v.actions.turnOff;
        nodes.check.hidden = !v.actions.check;
        for (const [button, name] of [[nodes.enable, "enable"], [nodes.off, "turn-off"], [nodes.check, "validate"]]) {
            button.disabled = v.busy !== "";
            if (v.busy === name) button.textContent = name === "enable" ? "Waiting for your approval…" : "Working…";
        }
        if (v.busy === "") {
            nodes.enable.textContent = "Enable tracing";
            nodes.off.textContent = "Turn off tracing";
            nodes.check.textContent = "Check connections";
        }
        nodes.endpoints.replaceChildren(...v.endpoints.map((e) => { const li = el("li", "connect__app", e.text); li.dataset.state = e.state; return li; }));
        nodes.apps.replaceChildren(...v.apps.map((app) => {
            const li = el("li", "connect__app");
            li.dataset.allProducing = String(app.allProducing);
            li.append(el("strong", "connect__app-name", app.summary));
            li.append(el("div", "connect__app-services", app.lines.join(" · ")));
            if (app.missing.length) {
                const button = el("button", "secondary-button", "How to set this up");
                button.type = "button";
                button.addEventListener("click", () => openGuidance(app.namespace));
                li.append(button);
            }
            return li;
        }));
    }

    const bridge = () => window.__resilienceTraceService || { available: false };
    panel = createPanel({
        get traceService() { return bridge(); },
        fetchJson,
        namespaces: () => (window.__resilienceRefresher ? window.__resilienceRefresher.snapshot() : null),
        now: () => Date.now(),
        onUpdate: render,
    });

    nodes.enable.addEventListener("click", () => { panel.enable(); });
    nodes.off.addEventListener("click", () => { panel.turnOff(); });
    nodes.check.addEventListener("click", () => { panel.validate(); });
    nodes.copy.addEventListener("click", () => {
        if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(nodes.guidanceSnippet.textContent).catch(() => {});
    });

    async function start() {
        const first = await panel.refresh();
        if (first.actions.check && first.reachable) await panel.validate();   // tests the addresses once it answers
        await panel.applications();
    }
    document.addEventListener("DOMContentLoaded", () => { start(); });
    setInterval(() => { if (!document.hidden) panel.refresh(); }, REFRESH_MS);
    setInterval(() => { if (!document.hidden) panel.applications(); }, APPS_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) { panel.refresh(); panel.applications(); } });
    window.addEventListener("resilience-cluster-changed", () => { panel.applications(); });
})();
