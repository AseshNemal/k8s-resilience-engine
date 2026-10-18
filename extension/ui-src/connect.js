/*
 * "Connect cluster" page logic (extension-only).
 * Talks to the extension backend through the fetch adapter in shim.js. It never displays or
 * logs the kubeconfig: the file is read once, sent, and discarded.
 */
(function () {
    const root = document;
    const el = (role) => root.querySelector(`[data-role="${role}"]`);

    const badge = el("connect-badge");
    const details = el("connect-details");
    const message = el("connect-message");
    const disconnect = el("connect-disconnect");
    const file = el("connect-file");
    const submit = el("connect-submit");

    function setMessage(text) {
        message.textContent = text || "";
    }

    function setBadge(text, kind) {
        badge.textContent = text;
        badge.className = `status-badge status-badge--${kind}`;
    }

    function render(status) {
        if (!status.connected) {
            setBadge("Not connected", "muted");
            details.hidden = true;
            disconnect.hidden = true;
            return;
        }
        details.hidden = false;
        disconnect.hidden = false;
        el("connect-server").textContent = status.server;
        el("connect-credential").textContent = status.credential;
        el("connect-expiry").textContent = status.expiresAt
            ? new Date(status.expiresAt).toLocaleString()
            : "No expiry information";
        if (status.expired) {
            setBadge("Credential expired", "danger");
            setMessage("Run ./scripts/extension-cluster-access.sh to renew, then choose the new file.");
        } else {
            setBadge("Connected (read-only)", "success");
        }
    }

    async function errorText(response) {
        try {
            const body = await response.json();
            if (typeof body.detail === "string") return body.detail;
        } catch (_ignored) {
            /* fall through */
        }
        return `Request failed with HTTP ${response.status}.`;
    }

    async function refresh() {
        try {
            const response = await fetch("/_extension/cluster");
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
        } catch (error) {
            setBadge("Backend unavailable", "danger");
            setMessage(error.message);
        }
    }

    file.addEventListener("change", () => {
        submit.disabled = file.files.length === 0;
        setMessage("");
    });

    submit.addEventListener("click", async () => {
        if (file.files.length === 0) return;
        submit.disabled = true;
        setMessage("Checking the credential…");
        try {
            const text = await file.files[0].text();
            const response = await fetch("/_extension/cluster", {
                method: "PUT",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ kubeconfig: text }),
            });
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
            setMessage("Connected. The Deployment Topology page can now use the live cluster.");
        } catch (error) {
            setMessage(error.message);
        } finally {
            file.value = "";
            submit.disabled = true;
        }
    });

    disconnect.addEventListener("click", async () => {
        try {
            const response = await fetch("/_extension/cluster", { method: "DELETE" });
            if (!response.ok) throw new Error(await errorText(response));
            render(await response.json());
            setMessage("Disconnected. The stored credential was deleted.");
        } catch (error) {
            setMessage(error.message);
        }
    });

    refresh();
})();
