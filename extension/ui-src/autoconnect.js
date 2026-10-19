/*
 * Automatic cluster connection and credential renewal (extension-only).
 *
 * The page asks the host helper (resilience-helper, launched through Docker Desktop) to find local
 * clusters, to set access up once after a native approval dialog, and to renew the short-lived
 * read-only credential. This file holds no credential and cannot receive one: the helper hands the
 * token straight to the backend and prints only a status. It never logs helper output, and shows
 * only the fixed messages below.
 *
 * No background daemon: renewal runs only while an extension page is open, on a 60 second check and
 * when the tab becomes visible. After the app was closed (a Docker Desktop restart, an extension
 * update, a long gap) the first check on the next open finds an expired credential and renews it.
 *
 * Renewal never asks the user anything and never recreates removed access: if the access was removed
 * or changed in the cluster, the page says so and waits for the user to approve again.
 */
(function () {
    "use strict";

    // Automatic setup is offered only where it was tested on a real machine. Add an operating system
    // here only after it was verified there (see docs/EXTENSION.md); until then it gets the manual path.
    const VERIFIED_PLATFORMS = ["darwin"];
    const POLL_MS = 60000;
    const BACKOFF_MS = [30000, 60000, 120000, 300000];
    // Problems a person has to fix. Retrying would not help, so the loop stops until they act.
    const NEEDS_USER = new Set(["authorization-revoked", "drifted", "over-granted", "rbac-denied", "not-authorized", "unsupported-context"]);

    const MESSAGES = {
        connected: "Connected.",
        disconnected: "Access was removed and the extension was disconnected.",
        declined: "You declined, so nothing was changed.",
        "timed-out": "No answer was given in time, so nothing was changed.",
        "approval-unavailable": "No approval dialog is available on this system. Use the manual connection instead.",
        "unsupported-context": "Automatic setup supports local kind clusters only. Choose another cluster or use the manual connection.",
        "cluster-unreachable": "The cluster is not reachable. Start Docker Desktop and the kind cluster; this will keep trying.",
        "rbac-denied": "Your kubeconfig is not allowed to manage access on this cluster. Ask a cluster administrator, or use the manual connection.",
        "over-granted": "Another role binding gives the extension's service account extra access, so nothing was changed. Remove that binding, then try again.",
        drifted: "The extension's access in the cluster was changed from what you approved, so renewal stopped. Review it, then approve again.",
        "authorization-revoked": "The extension's access was removed from the cluster. Approve it again to reconnect.",
        "not-authorized": "The extension is not set up for this cluster. Approve it first.",
        "backend-unavailable": "The extension backend is not responding yet. This will keep trying.",
        "helper-unavailable": "The helper could not be started. Reinstall the extension, or use the manual connection.",
        error: "Something went wrong. Try again, or use the manual connection.",
    };

    // Docker Desktop reports the host OS as ddClient.host.platform; fall back to the browser's own
    // answer in case it does not.
    function platformOf(dd, nav) {
        const given = dd && dd.host && typeof dd.host.platform === "string" ? dd.host.platform : "";
        if (given) return given;
        const hint = String((nav && nav.platform) || "");
        if (/Mac/i.test(hint)) return "darwin";
        if (/Win/i.test(hint)) return "win32";
        if (/Linux/i.test(hint)) return "linux";
        return "";
    }

    function platformSupport(platform) {
        if (VERIFIED_PLATFORMS.includes(platform)) return { automatic: true, note: "" };
        const names = { win32: "Windows", linux: "Linux", darwin: "macOS" };
        const name = names[platform] || "this operating system";
        return {
            automatic: false,
            note: `Automatic setup has not been verified on ${name} yet, so it is turned off. Use the manual connection below.`,
        };
    }

    function helperFile(platform) {
        return platform === "win32" ? "resilience-helper.exe" : "resilience-helper";
    }

    function messageFor(state) {
        return MESSAGES[state] || MESSAGES.error;
    }

    // The helper prints one JSON line. Docker Desktop may hand it back as stdout of a resolved call or
    // inside a rejection, so both are read; anything unreadable becomes "helper-unavailable".
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

    // env: { platform, exec(file, args) -> Promise<{stdout}>, getStatus() -> Promise<status|null>,
    //        now() -> ms, onChange(snapshot) }
    function createController(env) {
        let phase = "idle";
        let busy = false;
        let failures = 0;
        let retryAt = 0;
        let blocked = null; // { context, state } while a person has to act
        let last = { state: "", message: "" };
        let context = "";
        let expiresAt = "";

        function snapshot() {
            return { phase, state: last.state, message: last.message, context, expiresAt, busy };
        }
        function set(newPhase, state) {
            phase = newPhase;
            last = { state: state || "", message: state ? messageFor(state) : "" };
            if (env.onChange) env.onChange(snapshot());
        }

        async function runHelper(args) {
            const file = helperFile(env.platform);
            try {
                const out = await env.exec(file, args);
                return parseHelperOutput(out && out.stdout) || { ok: false, state: "helper-unavailable" };
            } catch (error) {
                return parseHelperOutput(error && error.stdout) || { ok: false, state: "helper-unavailable" };
            }
        }

        async function renew(name) {
            busy = true;
            phase = "renewing";
            const result = await runHelper(["renew", name]);
            busy = false;
            if (result.ok) {
                failures = 0;
                retryAt = 0;
                blocked = null;
                set("ready", "");
            } else if (NEEDS_USER.has(result.state)) {
                blocked = { context: name, state: result.state };
                set("attention", result.state);
            } else {
                failures += 1;
                retryAt = env.now() + BACKOFF_MS[Math.min(failures - 1, BACKOFF_MS.length - 1)];
                set("retrying", result.state);
            }
            return snapshot();
        }

        // One check. It never asks the user anything; it only renews a credential the user already approved.
        // The whole check is single-flight: the 60 second timer and a tab becoming visible can coincide,
        // and both must share one check rather than each fetching the status and each starting a renewal.
        let inFlight = null;
        function tick() {
            if (!inFlight) inFlight = check().finally(() => { inFlight = null; });
            return inFlight;
        }

        async function check() {
            if (busy) return snapshot();
            const status = await env.getStatus();
            if (!status) {
                set("retrying", "backend-unavailable");
                return snapshot();
            }
            context = status.context || "";
            expiresAt = status.expiresAt || "";
            if (!status.connected) {
                blocked = null;
                failures = 0;
                retryAt = 0;
                set("needs-setup", "");
                return snapshot();
            }
            if (!status.managed) {
                set("manual", "");
                return snapshot();
            }
            if (blocked && blocked.context !== status.context) blocked = null;
            if (blocked) {
                set("attention", blocked.state);
                return snapshot();
            }
            const due = status.state === "renew-soon" || status.state === "expired";
            if (!due) {
                failures = 0;
                retryAt = 0;
                set("ready", "");
                return snapshot();
            }
            if (env.now() < retryAt) return snapshot();
            return renew(status.context);
        }

        // The user's action: discover, set up, remove. Each clears a "needs the user" block.
        async function discover() {
            const result = await runHelper(["discover"]);
            return result;
        }

        async function setup(name) {
            if (busy) return { ok: false, state: "error" };
            busy = true;
            const result = await runHelper(["authorize", name]);
            busy = false;
            if (result.ok) {
                blocked = null;
                failures = 0;
                retryAt = 0;
                set("ready", "");
            } else if (result.state !== "declined" && result.state !== "timed-out") {
                // A declined or timed-out approval is not an error: nothing changed, so leave the page as it was.
                set("attention", result.state);
            }
            if (env.onChange) env.onChange(snapshot());
            return result;
        }

        // One-click setup from the status banner: find the cluster the stored credential points at and
        // run the normal setup, which still waits for the native approval dialog. A kind cluster's API
        // server port is what ties host.docker.internal:PORT (the backend's view) to 127.0.0.1:PORT
        // (the host's view). With no match and several candidates it never guesses: the caller sends
        // the person to the Connect page to choose.
        async function quickSetup(storedServer) {
            const found = await discover();
            if (!found.ok || !Array.isArray(found.contexts)) return found;
            const usable = found.contexts.filter((c) => c.supported && c.reachable === true);
            if (!usable.length) return { ok: false, state: "cluster-unreachable" };
            const port = (text) => { const m = /:(\d+)\/?$/.exec(String(text || "")); return m ? m[1] : ""; };
            const wanted = port(storedServer);
            const same = wanted ? usable.filter((c) => port(c.server) === wanted) : [];
            const pick = same.length === 1 ? same[0] : usable.length === 1 ? usable[0] : null;
            if (!pick) return { ok: false, state: "choose" };
            return setup(pick.name);
        }

        async function remove(name) {
            if (busy) return { ok: false, state: "error" };
            busy = true;
            const result = await runHelper(["revoke", name]);
            busy = false;
            if (result.ok) {
                blocked = null;
                failures = 0;
                retryAt = 0;
            }
            if (env.onChange) env.onChange(snapshot());
            return result;
        }

        function retryNow() {
            blocked = null;
            retryAt = 0;
            return tick();
        }

        return { tick, discover, setup, quickSetup, remove, retryNow, snapshot };
    }

    window.__resilienceAuto = {
        VERIFIED_PLATFORMS, POLL_MS, BACKOFF_MS, platformOf, platformSupport, helperFile, messageFor, parseHelperOutput, createController,
    };
    if (!window.document || !window.document.addEventListener) return;

    // ---- browser wiring: only where the host helper exists and automatic setup was verified ----
    const dd = window.ddClient;
    const platform = platformOf(dd, window.navigator);
    const support = platformSupport(platform);
    const execFn = dd && dd.extension && dd.extension.host && dd.extension.host.cli && dd.extension.host.cli.exec;
    window.__resilienceAutoSupport = { platform, automatic: support.automatic && Boolean(execFn), note: support.note, helperPresent: Boolean(execFn) };
    if (!support.automatic || !execFn) return;

    async function getStatus() {
        try {
            const response = await fetch("/_extension/cluster");
            return response.ok ? await response.json() : null;
        } catch (_error) {
            return null;
        }
    }

    const controller = createController({
        platform,
        exec: (file, args) => execFn(file, args),
        getStatus,
        now: () => Date.now(),
        // Tell the header chip and Overview panel to re-read the status after any change.
        onChange: () => window.dispatchEvent(new CustomEvent("resilience-cluster-changed")),
    });
    window.__resilienceController = controller;

    let timer = null;
    async function loop() {
        try { await controller.tick(); } finally { timer = setTimeout(loop, POLL_MS); }
    }
    document.addEventListener("DOMContentLoaded", loop);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && !controller.snapshot().busy) {
            clearTimeout(timer);
            loop();
        }
    });
})();
