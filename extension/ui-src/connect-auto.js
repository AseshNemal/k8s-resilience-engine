/*
 * "Set up automatically" card on the Connect page (extension-only).
 *
 * Presentation over autoconnect.js: list the local clusters the helper found, say for each one whether it is
 * Connected, Not connected, being reconnected or disconnected by the person, let the person pick one and
 * connect it (the approval itself happens in a native system dialog, not here), show renewal problems, and offer
 * to remove access. A newly started cluster appears by itself: the list is read again every minute while the
 * page is visible. Finding a cluster never connects it and never changes it; only the person's click, and
 * their approval in the dialog, does. Cluster and context names come from the user's kubeconfig, so
 * they are untrusted: everything is written with textContent, never innerHTML. No credential ever
 * reaches this page, so there is nothing here that could show one.
 */
(function () {
    "use strict";
    const q = (role) => document.querySelector(`[data-role="${role}"]`);

    const card = q("auto-card");
    if (!card) return;
    const support = window.__resilienceAutoSupport;
    const auto = window.__resilienceAuto;
    const controller = window.__resilienceController;

    const note = q("auto-note");
    const controls = q("auto-controls");
    const list = q("auto-clusters");
    const refreshButton = q("auto-refresh");
    const connectButton = q("auto-connect");
    const removeButton = q("auto-remove");
    const message = q("auto-message");
    const attention = q("auto-attention");
    const attentionText = q("auto-attention-text");
    const retryButton = q("auto-retry");

    const AUTO_REFRESH_MS = 60000;

    const ACCESS = {
        approved: "Access already set up",
        "approved-unlabelled": "Access exists from an earlier setup",
        absent: "Access not set up yet",
        partial: "Access partly removed; setting up restores it",
        drifted: "Access needs updating; setting up resets it to the current read-only set",
        "over-granted": "Another binding gives it extra access; remove that first",
    };

    function say(text, kind) {
        message.textContent = text || "";
        message.dataset.kind = kind || "";
    }

    function setBusy(busy) {
        refreshButton.disabled = busy;
        removeButton.disabled = busy;
        connectButton.disabled = busy || !selected();
    }

    function selected() {
        const input = list.querySelector('input[name="auto-context"]:checked');
        return input ? input.value : "";
    }

    function item(context, status) {
        const li = document.createElement("li");
        li.className = "connect__cluster";
        const badge = auto.clusterBadge(context, status);
        const label = document.createElement("label");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "auto-context";
        radio.value = context.name;
        radio.disabled = !badge.connectable;
        const name = document.createElement("span");
        name.className = "connect__cluster-name";
        name.textContent = context.name;
        const state = document.createElement("span");
        state.className = "connect__cluster-badge";
        state.dataset.kind = badge.kind;
        state.textContent = badge.text;
        const meta = document.createElement("span");
        meta.className = "connect__cluster-meta";
        if (!context.supported) {
            meta.textContent = context.reason || "Not supported for automatic setup";
        } else if (context.reachable !== true) {
            meta.textContent = `${context.server} · not reachable. Start Docker Desktop and the cluster.`;
        } else {
            const access = ACCESS[context.authorization] || "Access state unknown";
            meta.textContent = `${context.server} · Kubernetes ${context.serverVersion || ""} · ${access}`.replace("  ", " ");
        }
        label.append(radio, name, state, meta);
        li.append(label);
        li.dataset.usable = String(badge.connectable);
        li.dataset.state = badge.kind;
        return li;
    }

    async function readStatus() {
        try {
            const response = await fetch("/_extension/cluster");
            return response.ok ? await response.json() : null;
        } catch (_error) {
            return null;
        }
    }

    // `quiet` is the automatic refresh: no "Looking…" message and no flicker, and the person's choice stays.
    let looking = false;
    let lastFound = null;
    function showList(result, status) {
        const chosen = selected();
        list.replaceChildren();
        const contexts = result.contexts.slice().sort((a, b) => Number(b.supported) - Number(a.supported) || a.name.localeCompare(b.name));
        contexts.forEach((c) => list.append(item(c, status)));
        const usable = contexts.filter((c) => auto.clusterBadge(c, status).connectable);
        const keep = chosen && list.querySelector(`input[name="auto-context"]:not(:disabled)[value="${CSS.escape(chosen)}"]`);
        if (keep) keep.checked = true;
        else if (usable.length === 1) list.querySelector('input[name="auto-context"]:not(:disabled)').checked = true;
        connectButton.disabled = !selected();
        const running = contexts.filter((c) => c.supported && c.reachable === true);
        say(running.length ? "" : "No supported cluster is running. Start Docker Desktop's Kubernetes or your kind cluster; it appears here by itself.");
    }

    async function findClusters(quiet) {
        if (looking) return;
        looking = true;
        if (!quiet) {
            setBusy(true);
            say("Looking for clusters…");
            list.replaceChildren();
        }
        const [result, status] = await Promise.all([controller.discover(), readStatus()]);
        looking = false;
        if (!quiet) setBusy(false);
        if (!result.ok || !Array.isArray(result.contexts)) {
            if (!quiet) say(auto.messageFor(result.state), "error");
            return;
        }
        lastFound = result;
        showList(result, status);
    }

    // The status changed (connected, renewed, disconnected): relabel the list without asking the helper again.
    async function relabel() {
        if (!lastFound || looking) return;
        showList(lastFound, await readStatus());
    }

    async function refreshAttention() {
        const snap = controller.snapshot();
        const needs = snap.phase === "attention" || (snap.phase === "retrying" && snap.message);
        attention.hidden = !needs;
        attentionText.textContent = needs ? snap.message : "";
        retryButton.hidden = snap.phase !== "attention";
        try {
            const response = await fetch("/_extension/cluster");
            const status = response.ok ? await response.json() : null;
            removeButton.hidden = !(status && status.connected && status.managed);
            removeButton.dataset.context = status && status.managed ? status.context || "" : "";
        } catch (_error) {
            removeButton.hidden = true;
        }
    }

    // ---- show the card, honestly, on every platform ----
    card.hidden = false;
    if (!support || !support.automatic) {
        controls.hidden = true;
        note.textContent = !support || !support.helperPresent
            ? "Automatic setup needs the Docker Desktop extension host helper, which is not available here. Use the manual connection below."
            : support.note;
        return;
    }
    note.textContent = "";

    list.addEventListener("change", () => { connectButton.disabled = !selected(); });
    refreshButton.addEventListener("click", () => findClusters(false));

    connectButton.addEventListener("click", async () => {
        const name = selected();
        if (!name) return;
        setBusy(true);
        say("Waiting for your answer in the system dialog…");
        const result = await controller.setup(name);
        setBusy(false);
        if (result.ok) {
            const where = result.background === "on"
                ? "in the background, even when Docker Desktop is closed"
                : "while the extension is open";
            say(`Connected. Deployment topology can now show your live cluster, and the credential renews itself ${where}.`, "ok");
        } else if (result.state === "declined" || result.state === "timed-out") {
            say(auto.messageFor(result.state), "");
        } else {
            say(auto.messageFor(result.state), "error");
        }
        await refreshAttention();
    });

    removeButton.addEventListener("click", async () => {
        const name = removeButton.dataset.context || selected();
        if (!name) return;
        setBusy(true);
        say("Waiting for your answer in the system dialog…");
        const result = await controller.remove(name);
        setBusy(false);
        say(result.ok ? auto.messageFor("disconnected") : auto.messageFor(result.state), result.ok ? "ok" : "");
        await refreshAttention();
    });

    retryButton.addEventListener("click", async () => {
        retryButton.disabled = true;
        await controller.retryNow();
        retryButton.disabled = false;
        await refreshAttention();
    });

    window.addEventListener("resilience-cluster-changed", () => { refreshAttention(); relabel(); });
    findClusters(false).then(refreshAttention);

    // Detect a cluster that starts later, without being asked, while the person can see the page. Never while
    // a dialog is open (the buttons are disabled then) and never overlapping a search that is still running.
    setInterval(() => { if (!document.hidden && !refreshButton.disabled) findClusters(true); }, AUTO_REFRESH_MS);
    document.addEventListener("visibilitychange", () => { if (!document.hidden && !refreshButton.disabled) findClusters(true); });
})();
