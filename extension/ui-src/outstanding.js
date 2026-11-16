/* Outstanding applied changes on the Connect page. Reads and resets only after an explicit choice. */
(function () {
    "use strict";

    const LEDGER_URL = "/_extension/cluster/outstanding";
    const POLL_MS = 2000;
    const POLL_ATTEMPTS = 300;

    async function responseError(response) {
        let body = null;
        try { body = await response.json(); } catch (_ignored) { /* use the status below */ }
        const detail = body && body.detail;
        const message = typeof detail === "string" ? detail : detail && typeof detail.message === "string" ? detail.message : "";
        const error = new Error(message || `The request failed with HTTP ${response.status}.`);
        error.code = detail && typeof detail.code === "string" ? detail.code : "";
        error.status = response.status;
        throw error;
    }

    function createClient(env) {
        const request = env.request || (async (url, init) => {
            const response = await (env.fetch || fetch)(url, init);
            if (!response.ok) await responseError(response);
            return response.json();
        });
        const sleep = env.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));

        async function read() {
            const ledger = await request(LEDGER_URL);
            if (!ledger || !Array.isArray(ledger.changes) || !Array.isArray(ledger.blocking)) {
                throw new Error("The outstanding-changes answer was not in the expected form. Disconnect was not attempted.");
            }
            return ledger;
        }

        async function resetAll(blocking) {
            for (const change of blocking || []) {
                if (change.resetInProgress) continue;
                if (!change.canReset) {
                    throw new Error(`${change.recommendationId || change.applicationId} cannot be reset here. Reconnect its cluster and check write access; nothing was disconnected.`);
                }
                const base = change.tier === "research" ? "/api/recommendations/declared/" : "/api/recommendations/";
                await request(`${base}${encodeURIComponent(change.recommendationId)}/reset`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ resetBy: change.appliedBy || "Reset and disconnect", external: "refuse", expectedApplicationId: change.applicationId }),
                });
            }
        }

        async function waitSettled() {
            let latest = null;
            const attempts = env.pollAttempts || POLL_ATTEMPTS;
            for (let attempt = 0; attempt < attempts; attempt += 1) {
                latest = await read();
                if (latest.blocking.length === 0 && !latest.resetInProgress) return latest;
                await sleep(env.pollMs || POLL_MS);
            }
            throw new Error("The reset has not finished yet. The cluster is still connected; wait for the rollout and try Disconnect again.");
        }

        return { read, resetAll, waitSettled };
    }

    window.__resilienceOutstanding = { createClient, responseError, LEDGER_URL };
    if (!window.document || !window.document.querySelector) return;

    const q = (role) => document.querySelector(`[data-role="${role}"]`);
    const card = q("outstanding-card");
    if (!card) return;
    const list = q("outstanding-list");
    const status = q("outstanding-status");
    const ask = q("outstanding-ask");
    const reset = q("outstanding-reset");
    const keep = q("outstanding-keep");
    const cancel = q("outstanding-cancel");
    const client = createClient({});
    let pending = null;

    function text(tag, className, value) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        node.textContent = value;
        return node;
    }

    function say(value, kind) {
        status.textContent = value || "";
        status.dataset.kind = kind || "";
    }

    function clusterText(change) {
        const cluster = change.cluster || {};
        const name = cluster.context || cluster.name || cluster.clusterUid || "unknown cluster";
        const where = change.clusterMatch === "same" ? "connected cluster" : change.clusterMatch === "other" ? "another cluster" : "cluster identity unavailable";
        return `${name} · ${where}`;
    }

    function draw(ledger) {
        card.hidden = ledger.changes.length === 0;
        list.replaceChildren(...ledger.changes.map((change) => {
            const row = text("li", "connect__app");
            const summaries = (change.changes || []).map((item) => item.summary).filter(Boolean);
            row.append(
                text("strong", "connect__cluster-name", summaries.join("; ") || change.recommendationId || change.applicationId),
                text("div", "connect__app-meta", `${change.status} · ${change.namespace || "namespace unknown"} · ${clusterText(change)}`),
            );
            if (change.kept) row.append(text("div", "connect__app-meta", "Kept by a named decision; it can still be reset."));
            if (change.resetInProgress) row.append(text("div", "connect__app-warning", "Reset rollout is still being verified."));
            return row;
        }));
        if (ledger.changes.length) {
            const blocking = ledger.blocking.length;
            say(blocking ? `${blocking} change${blocking === 1 ? " is" : "s are"} still applied to this cluster. Reset or explicitly keep ${blocking === 1 ? "it" : "them"} before disconnecting.` : "No applied change blocks disconnect. Kept and completed changes remain listed here for review.");
        } else {
            say("");
        }
    }

    async function refresh() {
        try {
            const ledger = await client.read();
            draw(ledger);
            return ledger;
        } catch (error) {
            if (error.code === "CLUSTER_NOT_CONNECTED") {
                draw({ changes: [], blocking: [] });
                return { changes: [], blocking: [], resetInProgress: false };
            }
            card.hidden = false;
            list.replaceChildren();
            say(error.message, "error");
            throw error;
        }
    }

    function setBusy(busy) {
        reset.disabled = busy;
        keep.disabled = busy;
        cancel.disabled = busy;
    }

    function askAbout(ledger) {
        ask.hidden = false;
        reset.hidden = false;
        keep.hidden = false;
        cancel.hidden = false;
        reset.disabled = ledger.resetInProgress || ledger.blocking.some((change) => !change.resetInProgress && !change.canReset);
        keep.disabled = Boolean(ledger.resetInProgress);
        if (ledger.resetInProgress) say("A reset is still rolling out. Wait for it to finish; the credential cannot be removed yet.", "error");
        return new Promise((resolve) => {
            const finish = (answer) => {
                cancel.removeEventListener("click", onCancel);
                keep.removeEventListener("click", onKeep);
                reset.removeEventListener("click", onReset);
                ask.hidden = true;
                pending = null;
                resolve(answer);
            };
            const onCancel = () => finish("cancel");
            const onKeep = () => { if (!keep.disabled) finish("proceed-keep"); };
            const onReset = async () => {
                if (reset.disabled) return;
                setBusy(true);
                say("Resetting the applied changes and waiting for their rollouts…");
                try {
                    await client.resetAll(ledger.blocking);
                    await client.waitSettled();
                    finish("proceed");
                } catch (error) {
                    finish("cancel");
                    setBusy(false);
                    say(error.message, "error");
                }
            };
            cancel.addEventListener("click", onCancel);
            keep.addEventListener("click", onKeep);
            reset.addEventListener("click", onReset);
        });
    }

    async function beforeDisconnect() {
        if (pending) return pending;
        pending = (async () => {
            let ledger;
            try { ledger = await refresh(); } catch (_error) { pending = null; return "cancel"; }
            if (!ledger.blocking.length && !ledger.resetInProgress) {
                pending = null;
                return "proceed";
            }
            return askAbout(ledger);
        })();
        return pending;
    }

    window.__resilienceOutstanding.page = { refresh, beforeDisconnect };
    window.addEventListener("resilience-cluster-changed", () => { refresh().catch(() => {}); });
    refresh().catch(() => {});
})();
