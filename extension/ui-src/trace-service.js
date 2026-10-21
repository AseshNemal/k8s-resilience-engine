/*
 * Starts and stops the trace service (Jaeger) for the Workflow dependencies page (extension-only).
 *
 * A web page cannot start a container, so the page asks the host helper (resilience-helper, launched through
 * Docker Desktop) to do it: `trace-status`, `trace-start` and `trace-stop`. The helper starts one fixed
 * container and nothing else; see helper/internal/tracesvc. This file passes no argument to the helper and
 * shows only the fixed messages below, never helper or Docker text.
 *
 * It exposes window.__resilienceTraceService = { available, status(), start(), stop() }. Where the host
 * helper does not exist (the app opened in a plain browser) `available` is false and the page says how to
 * start a trace service by hand instead.
 */
(function () {
    "use strict";

    // How long to wait for each helper command before giving up on that call. They sit above the helper's own
    // deadlines (5 minutes for a start that has to pull the image).
    const WAIT_MS = { "trace-status": 45000, "trace-start": 330000, "trace-stop": 90000 };
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
            return { ok: parsed.ok === true, state: parsed.state, message: messageFor(parsed.state) };
        }

        // One helper call at a time: a second click while one runs shares its answer instead of starting another.
        function single(command) {
            if (!available) {
                return Promise.resolve({ ok: false, state: "helper-unavailable", message: messageFor("helper-unavailable") });
            }
            if (inFlight) return inFlight.promise;
            const promise = runHelper(command).finally(() => { inFlight = null; });
            inFlight = { command, promise };
            return promise;
        }

        return {
            available,
            status: () => single("trace-status"),
            start: () => single("trace-start"),
            stop: () => single("trace-stop"),
        };
    }

    window.__resilienceTrace = { createTraceService, messageFor, parseHelperOutput, helperFile, platformOf, WAIT_MS };
    if (!window.document || !window.document.addEventListener) return;

    const dd = window.ddClient;
    const execFn = dd && dd.extension && dd.extension.host && dd.extension.host.cli && dd.extension.host.cli.exec;
    window.__resilienceTraceService = createTraceService({
        platform: platformOf(dd, window.navigator),
        exec: execFn ? (file, args) => execFn(file, args) : null,
        sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
    });
})();
