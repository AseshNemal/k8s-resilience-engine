/*
 * Starts, stops, enables and checks the trace service (Jaeger) for the Workflow dependencies and Connect pages
 * (extension-only).
 *
 * A web page cannot start a container, so the page asks the host helper (resilience-helper, launched through
 * Docker Desktop) to do it: `trace-status`, `trace-start`, `trace-stop` and, for the one-time
 * approved setup and its automatic upkeep, `trace-enable`, `trace-ensure` and `trace-validate`. The helper starts one fixed
 * container and nothing else; see helper/internal/tracesvc. This file passes no argument to the helper and
 * shows only the fixed messages below, never helper or Docker text.
 *
 * It exposes window.__resilienceTraceService = { available, status(), start(), stop(), enable(), ensure(), validate() }. Where the host
 * helper does not exist (the app opened in a plain browser) `available` is false and the page says how to
 * start a trace service by hand instead.
 */
(function () {
    "use strict";

    // How long to wait for each helper command before giving up on that call. They sit above the helper's own
    // deadlines (5 minutes for a start that has to pull the image).
    const WAIT_MS = {
        "trace-status": 45000, "trace-start": 330000, "trace-stop": 90000,
        // enable shows a native dialog (up to 90 s) and may then pull the image, like a first start.
        "trace-enable": 330000, "trace-ensure": 330000, "trace-validate": 90000,
    };
    const COMMANDS = new Set(Object.keys(WAIT_MS));

    const MESSAGES = {
        "trace-running": "The trace service is running.",
        "trace-running-external": "A trace service is already running. It was started somewhere else, so it is left as it is.",
        "trace-stopped": "The trace service is stopped.",
        "trace-not-created": "The trace service has not been started yet.",
        "trace-not-ready": "The trace service was started but is not answering yet. Give it a moment, then try again.",
        "trace-port-in-use": "Another program is using the trace service's ports (16686, 4317 or 4318). Stop it, then try again.",
        "trace-start-failed": "Docker could not start the trace service.",
        "docker-unavailable": "Docker could not be reached, so the trace service cannot be started from here.",
        "trace-image-unavailable": "Docker could not download the trace service image. Check the network (a proxy or TLS inspection can block it), or run `docker pull jaegertracing/all-in-one:1.56`, then try again.",
        "trace-not-enabled": "Tracing is not turned on yet.",
        "trace-recovery-wait": "The trace service is not answering. It was recreated a moment ago, so it is left alone for a while before another try.",
        "trace-endpoints-ok": "The trace service's query and OTLP receivers answer on this computer.",
        "trace-endpoints-failed": "The trace service does not answer on every address, so applications cannot send traces yet.",
        "trace-approval-timed-out": "No answer was given, so nothing was started.",
        "declined": "You declined, so nothing was started.",
        "approval-unavailable": "No approval dialog is available on this system, so tracing cannot be turned on from here.",
        "helper-unavailable": "The extension helper could not be started. Start the trace service by hand instead.",
        "timed-out": "Docker Desktop did not answer in time. Try again in a moment.",
    };

    function messageFor(state) {
        return MESSAGES[state] || MESSAGES["helper-unavailable"];
    }

    // The helper prints one JSON line. Docker Desktop may hand it back as stdout of a resolved call or inside a
    // rejection, so both are read; anything unreadable becomes "helper-unavailable".
    function parseHelperOutput(text) {
        if (typeof text !== "string") return null;
        const line = text.trim().split("\n").pop();
        try {
            const value = JSON.parse(line);
            return value && typeof value === "object" && typeof value.state === "string" ? value : null;
        } catch (_error) {
            return null;
        }
    }

    // The helper's `trace` object, reduced to the fields this page knows. Anything else is dropped, and an address
    // or note is only ever shown as text, so a malformed answer cannot add anything to the page.
    const ENDPOINT_NAMES = new Set(["query", "otlp-http", "otlp-grpc", "pods-otlp-http"]);
    const EVIDENCE = new Set(["host", "node"]);

    function cleanTrace(value) {
        if (!value || typeof value !== "object") return null;
        const endpoints = Array.isArray(value.endpoints) ? value.endpoints : [];
        return {
            enabled: value.enabled === true,
            endpoints: endpoints
                .filter((e) => e && ENDPOINT_NAMES.has(e.name) && EVIDENCE.has(e.evidence))
                .map((e) => ({
                    name: e.name,
                    address: typeof e.address === "string" ? e.address.slice(0, 200) : "",
                    reachable: e.reachable === true ? true : e.reachable === false ? false : null,
                    evidence: e.evidence,
                    note: typeof e.note === "string" ? e.note.slice(0, 300) : "",
                })),
        };
    }

    const BACKGROUND = new Set(["on", "off", "stale", "unsupported"]);

    function helperFile(platform) {
        return platform === "win32" ? "resilience-helper.exe" : "resilience-helper";
    }

    function platformOf(dd, nav) {
        const given = dd && dd.host && typeof dd.host.platform === "string" ? dd.host.platform : "";
        if (given) return given;
        const hint = String((nav && nav.platform) || "");
        if (/Mac/i.test(hint)) return "darwin";
        if (/Win/i.test(hint)) return "win32";
        if (/Linux/i.test(hint)) return "linux";
        return "";
    }

    // env: { platform, exec(file, args), sleep(ms) }. Without env.exec nothing is available.
    function createTraceService(env) {
        const available = Boolean(env && typeof env.exec === "function");
        let inFlight = null;

        async function runHelper(command) {
            if (!COMMANDS.has(command)) throw new Error("unknown helper command");
            let work;
            try {
                work = Promise.resolve(env.exec(helperFile(env.platform), [command]));
            } catch (_error) {
                return { ok: false, state: "helper-unavailable", message: messageFor("helper-unavailable") };
            }
            const timeout = env.sleep ? env.sleep(WAIT_MS[command]).then(() => ({ timedOut: true })) : null;
            let raced;
            try {
                raced = await (timeout ? Promise.race([work, timeout]) : work);
            } catch (error) {
                // A non-zero exit can arrive as a rejection that still carries the helper's output.
                raced = { stdout: error && typeof error.stdout === "string" ? error.stdout : "" };
            }
            if (raced && raced.timedOut) return { ok: false, state: "timed-out", message: messageFor("timed-out") };
            const parsed = parseHelperOutput(raced && typeof raced.stdout === "string" ? raced.stdout : "");
            if (!parsed) return { ok: false, state: "helper-unavailable", message: messageFor("helper-unavailable") };
            // Only the state is taken from the helper; the wording is this file's own.
            return {
                ok: parsed.ok === true,
                state: parsed.state,
                message: messageFor(parsed.state),
                trace: cleanTrace(parsed.trace),
                background: BACKGROUND.has(parsed.background) ? parsed.background : "",
            };
        }

        // One helper call at a time: a second click while one runs shares its answer instead of starting another.
        // The commands that change something the person asked for (enable, ensure, validate) are different: if some
        // other call is running they wait for it and then run, so a click is never answered with another call's
        // result (for example a routine status check that happened to be in flight).
        function single(command, queue = false) {
            if (!available) {
                return Promise.resolve({ ok: false, state: "helper-unavailable", message: messageFor("helper-unavailable") });
            }
            if (inFlight && (inFlight.command === command || !queue)) return inFlight.promise;
            const previous = inFlight ? inFlight.promise.catch(() => null) : Promise.resolve();
            const entry = { command };
            entry.promise = previous.then(() => runHelper(command)).finally(() => { if (inFlight === entry) inFlight = null; });
            inFlight = entry;
            return entry.promise;
        }

        return {
            available,
            status: () => single("trace-status"),
            start: () => single("trace-start"),
            stop: () => single("trace-stop"),
            // Enabling asks the person in a native dialog the page cannot see or answer; ensure and validate never ask.
            enable: () => single("trace-enable", true),
            ensure: () => single("trace-ensure", true),
            validate: () => single("trace-validate", true),
        };
    }

    window.__resilienceTrace = { createTraceService, messageFor, parseHelperOutput, cleanTrace, helperFile, platformOf, WAIT_MS };
    if (!window.document || !window.document.addEventListener) return;

    const dd = window.ddClient;
    const execFn = dd && dd.extension && dd.extension.host && dd.extension.host.cli && dd.extension.host.cli.exec;
    window.__resilienceTraceService = createTraceService({
        platform: platformOf(dd, window.navigator),
        exec: execFn ? (file, args) => execFn(file, args) : null,
        sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    });
})();
