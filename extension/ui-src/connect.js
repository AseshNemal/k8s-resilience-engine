/*
 * "Connect cluster" page logic (extension-only).
 * Talks to the extension backend through the fetch adapter in shim.js. It never displays or
 * logs the kubeconfig: the file is read once, sent, and discarded.
 */
(function () {
    "use strict";
    const q = (role) => document.querySelector(`[data-role="${role}"]`);

    const status = q("connect-status");
    const title = q("connect-title");
    const summary = q("connect-summary");
    const details = q("connect-details");
    const message = q("connect-message");
    const disconnect = q("connect-disconnect");
    const file = q("connect-file");
    const fileName = q("connect-filename");
    const submit = q("connect-submit");

    // What to do next, for the failures people actually hit. Matched on the backend's own message.
    const HINTS = [
        [/can change the cluster/, "Run ./scripts/extension-cluster-access.sh (Windows: scripts\\extension-cluster-access.cmd) and choose the file it writes. It issues a time-limited credential."],
        [/Could not reach the cluster/, "Check that the cluster is running (./scripts/cluster-up.sh, or scripts\\cluster-up.cmd on Windows), then try again."],
        [/rejected the credential|expired/, "Run ./scripts/extension-cluster-access.sh (Windows: scripts\\extension-cluster-access.cmd) to issue a new token, then choose the new file."],
        [/current-context|valid YAML|not a kubeconfig|too large|embedded|https URL|exec|proxy|TLS/, "Choose the file written by ./scripts/extension-cluster-access.sh: .kube/extension-readonly.kubeconfig."],
    ];

    function say(text, kind) {
        message.replaceChildren();
        message.dataset.kind = kind || "";
        if (!text) return;
        message.append(document.createTextNode(text));
        const hint = kind === "error" ? (HINTS.find(([pattern]) => pattern.test(text)) || [])[1] : null;
        if (hint) {
            const line = document.createElement("span");
            line.className = "connect__hint";
            line.textContent = hint;
            message.append(line);
        }
    }

    function expiryText(iso) {
        const chrome = window.__resilienceChrome;
        return (chrome && chrome.formatExpiry(iso, new Date())) || (iso ? new Date(iso).toLocaleString() : "No expiry information");
    }

    function render(s) {
        const connected = Boolean(s.connected);
        const expired = connected && Boolean(s.expired);
        // A credential the helper installed renews itself, so it is never shown as expired or with an
        // expiry date: an expired one is simply being renewed.
        const managed = connected && Boolean(s.managed);
        status.dataset.state = !connected ? "none" : expired ? (managed ? "renewing" : "expired") : "connected";
        title.textContent = !connected ? "Not connected" : expired ? (managed ? "Renewing automatically" : "Credential expired") : "Connected · full access";
        summary.textContent = !connected
            ? "Choose a kubeconfig to connect your cluster. Until then pages report that no cluster is connected."
            : expired
                ? (managed
                    ? "The credential is being renewed automatically. No action is needed; pages show data again as soon as that finishes."
                    : "Replace the credential to keep reading your cluster.")
                : "The app reads your cluster, and can apply an approved change to it and reset it. Nothing is applied without an approved recommendation.";
        details.hidden = !connected;
        disconnect.hidden = !connected;
        submit.textContent = connected ? "Replace credential" : "Connect";
        if (connected) {
            q("connect-server").textContent = s.server;
            q("connect-credential").textContent = s.credential;
            q("connect-expiry").textContent = managed ? "Renews itself automatically" : expiryText(s.expiresAt);
        }
    }

    async function errorText(response) {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
        } catch (_ignored) {
            /* fall through */
        }
        return `The request failed with HTTP ${response.status}.`;
    }

    async function refresh() {
        try {
            const response = await fetch("/_extension/cluster");
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
        } catch (error) {
            status.dataset.state = "expired";
            title.textContent = "Backend unavailable";
            summary.textContent = "The extension backend is not responding. Wait a few seconds and reload.";
            say(error.message, "error");
        }
    }

    file.addEventListener("change", () => {
        const picked = file.files.length > 0;
        submit.disabled = !picked;
        fileName.textContent = picked ? file.files[0].name : "";
        say("");
    });

    submit.addEventListener("click", async () => {
        if (file.files.length === 0) return;
        submit.disabled = true;
        say("Checking the credential…");
        try {
            const text = await file.files[0].text();
            const response = await fetch("/_extension/cluster", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kubeconfig: text }),
            });
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
            say("Connected. Deployment topology can now show your live cluster.", "ok");
        } catch (error) {
            say(error.message, "error");
        } finally {
            file.value = "";
            fileName.textContent = "";
            submit.disabled = true;
        }
    });

    disconnect.addEventListener("click", async () => {
        try {
            const response = await fetch("/_extension/cluster", { method: "DELETE" });
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
            say("Disconnected. The stored credential was deleted.", "ok");
        } catch (error) {
            say(error.message, "error");
        }
    });

    window.addEventListener("resilience-cluster-changed", refresh);
    refresh();
})();
