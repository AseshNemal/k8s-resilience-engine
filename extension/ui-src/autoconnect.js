/*
 * Automatic cluster connection and credential renewal (extension-only).
 *
 * The page asks the host helper (resilience-helper, launched through Docker Desktop) to find local
 * clusters, to set access up once after a native approval dialog, and to renew the short-lived
 * read-only credential. This file holds no credential and cannot receive one: the helper hands the
 * token straight to the backend and prints only a status. It never logs helper output, and shows
 * only the fixed messages below.
 *
 * While an extension page is open it checks every 60 seconds (every 20 while a renewal is pending)
 * and when the tab becomes visible. When no page is open a small background job (a LaunchAgent, a Task Scheduler task or a systemd timer) renews the
 * credential instead (see helper/internal/agent); the person turns that on once, in a native dialog.
 * Either way, the first check after a long gap finds an expired credential and renews it.
 *
 * Every call to the host helper or the backend has a watchdog: a Docker Desktop that is busy can leave
 * a call unanswered for minutes, and without one the single-flight guard would wait on it forever and
 * nothing would ever renew again.
 *
 * Renewal never asks the user anything and never recreates removed access: if the access was removed
 * or changed in the cluster, the page says so and waits for the user to approve again.
 */
(function () {
    "use strict";

    // Automatic setup is offered on these systems. Only macOS has been tried on a real machine; Windows and
    // Linux are enabled on purpose before that (their jobs and dialogs are covered by tests and compile, see
    // docs/EXTENSION.md), so a problem there is expected to show up as a clear message, not silence. Any
    // other system gets the manual path.
    const ENABLED_PLATFORMS = ["darwin", "win32", "linux"];
    const POLL_MS = 60000;
    // While a renewal is pending (failed and waiting to retry, or running) look more often. The back-off
    // below still decides when a retry is actually allowed, so this never makes the helper run more.
    const PENDING_POLL_MS = 20000;
    const BACKOFF_MS = [30000, 60000, 120000, 300000];
    // How long to wait for each helper command before giving up on that call. They sit above the
    // helper's own deadlines (4 minutes for a renewal, a 90 second dialog for an approval).
    // When the helper reports there is nothing to reconnect (for example it sees the backend differently), ask
    // again only after this long, so an unusual state never turns into a request every minute.
    const IDLE_RETRY_MS = 300000;
    const WAIT_MS = { renew: 330000, reconnect: 330000, authorize: 360000, revoke: 360000, discover: 90000, status: 20000, "agent-status": 45000, "agent-enable": 360000, "agent-disable": 45000 };
    const DEFAULT_WAIT_MS = 360000;
    const TIMED_OUT = { timedOut: true };
    // Problems a person has to fix. Retrying would not help, so the loop stops until they act.
    const NEEDS_USER = new Set(["authorization-revoked", "drifted", "over-granted", "rbac-denied", "not-authorized", "unsupported-context", "identity-changed"]);

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
        "identity-changed": "The cluster behind this context is not the one you approved (its address or certificate authority changed), so it was not reconnected. Connect it again to approve it.",
        busy: "Another connection or renewal is running. This will try again by itself.",
        "backend-unavailable": "The extension backend is not responding yet. This will keep trying.",
        "too-slow": "Docker Desktop is responding very slowly, so the credential could not be renewed yet. It keeps trying by itself; closing other containers or giving Docker more memory makes it faster.",
        "background-unsupported": "Background renewal is not available on this operating system yet. The credential renews while the extension is open.",
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
        if (ENABLED_PLATFORMS.includes(platform)) return { automatic: true, note: "" };
        const names = { win32: "Windows", linux: "Linux", darwin: "macOS" };
        const name = names[platform] || "this operating system";
        return {
            automatic: false,
            note: `Automatic setup is not available on ${name}. Use the manual connection below.`,
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

    // Gives up on `work` after `ms`. Without env.sleep (a test, or a browser without timers) it just waits.
    // The abandoned call may still finish later; its answer is ignored, and a rejection is already handled.
    function raceTimeout(env, work, ms) {
        if (!env.sleep) return work;
        return Promise.race([work, env.sleep(ms).then(() => TIMED_OUT)]);
    }

    // env: { platform, exec(file, args) -> Promise<{stdout}>, getStatus() -> Promise<status|null>,
    //        now() -> ms, onChange(snapshot), sleep(ms) -> Promise (optional: the watchdog) }
    function createController(env) {
        let phase = "idle";
        let busy = false;
        let failures = 0;
        let retryAt = 0;
        let blocked = null; // { context, state } while a person has to act
        let last = { state: "", message: "" };
        let context = "";
        let expiresAt = "";
        let background = ""; // "" unknown | on | off | stale | unsupported: the background renewal job

        function snapshot() {
            return { phase, state: last.state, message: last.message, context, expiresAt, busy, background };
        }
        function set(newPhase, state) {
            phase = newPhase;
            last = { state: state || "", message: state ? messageFor(state) : "" };
            if (env.onChange) env.onChange(snapshot());
        }

        async function runHelper(args) {
            const file = helperFile(env.platform);
            try {
                const out = await raceTimeout(env, env.exec(file, args), WAIT_MS[args[0]] || DEFAULT_WAIT_MS);
                if (out === TIMED_OUT) return { ok: false, state: "too-slow" };
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
                if (env.onRenewed) env.onRenewed();
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

        // The connection was lost for a cluster the person approved and has not disconnected: ask the helper to
        // restore it. The helper checks that it is the same cluster and that the approved read-only access is
        // still exactly as approved, asks the cluster only for a token, and never recreates removed access.
        async function reconnect(name) {
            busy = true;
            phase = "reconnecting";
            const result = await runHelper(["reconnect"]);
            busy = false;
            if (result.ok && result.state === "connected") {
                failures = 0;
                retryAt = 0;
                blocked = null;
                set("ready", "");
                if (env.onRenewed) env.onRenewed();
            } else if (result.ok) {
                // Nothing to reconnect (the helper's view differs): wait quietly, do not ask every minute.
                failures = 0;
                retryAt = env.now() + IDLE_RETRY_MS;
                set("needs-setup", "");
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
            const answer = await raceTimeout(env, env.getStatus(), WAIT_MS.status);
            const status = answer === TIMED_OUT ? null : answer;
            if (!status) {
                set("retrying", "backend-unavailable");
                return snapshot();
            }
            context = status.context || "";
            expiresAt = status.expiresAt || "";
            if (!status.connected) {
                const approved = status.approved;
                if (!approved || approved.disconnectedByUser) {
                    // Never approved, or the person disconnected it: only the person can connect it.
                    context = approved ? approved.context || "" : context;
                    blocked = null;
                    failures = 0;
                    retryAt = 0;
                    set("needs-setup", "");
                    return snapshot();
                }
                context = approved.context || "";
                if (blocked && blocked.context !== approved.context) blocked = null;
                if (blocked) {
                    set("attention", blocked.state);
                    return snapshot();
                }
                if (env.now() < retryAt) return snapshot();
                return reconnect(approved.context);
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
                if (typeof result.background === "string") background = result.background;
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
                if (typeof result.background === "string") background = result.background;
            }
            if (env.onChange) env.onChange(snapshot());
            return result;
        }

        // Read-only: ask whether the background renewal job is on. Called by the page, never by a check,
        // so a credential that is not due still never starts the helper.
        async function refreshBackground() {
            if (busy) return snapshot();
            const result = await runHelper(["agent-status"]);
            if (result.ok && typeof result.background === "string") {
                background = result.background;
                if (env.onChange) env.onChange(snapshot());
            }
            return snapshot();
        }

        // The one-time consent for the background job: the helper shows a native dialog. Declining, or
        // not answering, changes nothing and is not an error.
        async function enableBackground(name) {
            if (busy) return { ok: false, state: "error" };
            busy = true;
            const result = await runHelper(["agent-enable", name]);
            busy = false;
            if (typeof result.background === "string" && result.background) background = result.background;
            else if (result.ok) background = "on";
            if (env.onChange) env.onChange(snapshot());
            return result;
        }

        function retryNow() {
            blocked = null;
            retryAt = 0;
            return tick();
        }

        return { tick, discover, setup, quickSetup, remove, retryNow, refreshBackground, enableBackground, snapshot };
    }

    window.__resilienceAuto = {
        ENABLED_PLATFORMS, POLL_MS, PENDING_POLL_MS, BACKOFF_MS, WAIT_MS, platformOf, platformSupport, helperFile, messageFor, parseHelperOutput, createController,
    };
    if (!window.document || !window.document.addEventListener) return;

    // ---- browser wiring: only where the host helper exists and automatic setup is enabled ----
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
        sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
        // Tell the header chip and Overview panel to re-read the status after any change.
        onChange: () => window.dispatchEvent(new CustomEvent("resilience-cluster-changed")),
        // A page that loaded while the credential was expired is showing an access error. Now that the
        // credential is fresh, press that page's own Retry so it loads by itself; nothing else is retried.
        onRenewed: () => {
            for (const button of document.querySelectorAll('.error-state[data-state="access-denied"] [data-role="retry-button"]')) button.click();
        },
    });
    window.__resilienceController = controller;

    let timer = null;
    let askedBackground = false;
    async function loop() {
        let delay = POLL_MS;
        try {
            const snap = await controller.tick();
            // Ask once per page load whether the background job is on, as soon as a managed credential exists.
            if (!askedBackground && snap.context && snap.phase !== "needs-setup" && snap.phase !== "manual") {
                askedBackground = true;
                await controller.refreshBackground();
            }
            if (snap.phase === "retrying" || snap.phase === "renewing") delay = PENDING_POLL_MS;
        } finally { timer = setTimeout(loop, delay); }
    }
    document.addEventListener("DOMContentLoaded", loop);
    document.addEventListener("visibilitychange", () => {
        if (!document.hidden && !controller.snapshot().busy) {
            clearTimeout(timer);
            loop();
        }
    });
})();
