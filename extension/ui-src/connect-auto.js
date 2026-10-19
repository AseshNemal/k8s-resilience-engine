/*
 * "Set up automatically" card on the Connect page (extension-only).
 *
 * Presentation over autoconnect.js: list the local clusters the helper found, let the person pick one
 * and start setup (the approval itself happens in a native system dialog, not here), show renewal
 * problems, and offer to remove access. Cluster and context names come from the user's kubeconfig, so
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

    const ACCESS = {
        approved: "Access already set up",
        "approved-unlabelled": "Access exists from an earlier setup",
        absent: "Access not set up yet",
        partial: "Access partly removed; setting up restores it",
        drifted: "Access was changed; setting up resets it to read-only",
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

    function item(context) {
        const li = document.createElement("li");
        li.className = "connect__cluster";
        const label = document.createElement("label");
        const radio = document.createElement("input");
        radio.type = "radio";
        radio.name = "auto-context";
        radio.value = context.name;
        const usable = context.supported && context.reachable === true;
        radio.disabled = !usable;
        const name = document.createElement("span");
        name.className = "connect__cluster-name";
        name.textContent = context.name;
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
        label.append(radio, name, meta);
        li.append(label);
        li.dataset.usable = String(usable);
        return li;
    }

    async function findClusters() {
        setBusy(true);
        say("Looking for clusters…");
        list.replaceChildren();
        const result = await controller.discover();
        setBusy(false);
        if (!result.ok || !Array.isArray(result.contexts)) {
            say(auto.messageFor(result.state), "error");
            return;
        }
        const contexts = result.contexts.slice().sort((a, b) => Number(b.supported) - Number(a.supported) || a.name.localeCompare(b.name));
        contexts.forEach((c) => list.append(item(c)));
        const usable = contexts.filter((c) => c.supported && c.reachable === true);
        if (usable.length === 1) {
            list.querySelector('input[name="auto-context"]:not(:disabled)').checked = true;
        }
        connectButton.disabled = !selected();
        say(usable.length ? "" : "No supported cluster is running. Start your kind cluster and press Find clusters.");
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
    refreshButton.addEventListener("click", findClusters);

    connectButton.addEventListener("click", async () => {
        const name = selected();
        if (!name) return;
        setBusy(true);
        say("Waiting for your answer in the system dialog…");
        const result = await controller.setup(name);
        setBusy(false);
        if (result.ok) {
            say("Connected. Deployment topology can now show your live cluster, and the credential renews itself while the extension is open.", "ok");
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

    window.addEventListener("resilience-cluster-changed", refreshAttention);
    findClusters().then(refreshAttention);
})();
