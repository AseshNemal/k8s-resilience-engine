/*
 * Extension chrome: the cluster-status chip in the header, the Overview connection panel, and
 * Escape-to-close for the approval dialog. Presentation only: it reads the existing
 * GET /_extension/cluster endpoint and changes no data.
 */
(function () {
    "use strict";

    function formatExpiry(iso, now) {
        if (!iso) return null;
        const when = new Date(iso);
        if (Number.isNaN(when.getTime())) return null;
        const date = when.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
        const hours = Math.round((when - now) / 3600000);
        let relative;
        if (hours <= 0) relative = "expired";
        else if (hours < 48) relative = `in ${hours} hour${hours === 1 ? "" : "s"}`;
        else relative = `in ${Math.round(hours / 24)} days`;
        return `${date} (${relative})`;
    }

    // One source of truth for how a connection state is worded, used by the chip and the Overview panel.
    function describeCluster(status, now) {
        if (!status) {
            return { state: "error", chip: "Backend unavailable", title: "The extension backend is not responding",
                detail: "Wait a few seconds and reload. If it persists, restart the extension.", action: null };
        }
        if (!status.connected) {
            return { state: "none", chip: "Not connected", title: "No cluster connected",
                detail: "Results here come from built-in sample data. Connect a read-only credential to see your own cluster on Deployment topology.",
                action: "Connect cluster" };
        }
        const expiry = formatExpiry(status.expiresAt, now);
        if (status.expired) {
            return { state: "expired", chip: "Credential expired", title: `Credential for ${status.server} has expired`,
                detail: "Renew it to read your cluster again. Results on this page are sample data either way.", action: "Renew credential" };
        }
        return { state: "connected", chip: "Connected · read-only", title: `Connected to ${status.server}`,
            detail: `Read-only access${expiry ? `, credential expires ${expiry}` : ""}. Overview and the research pages show sample data; Deployment topology can show your live cluster.`,
            action: "Manage connection" };
    }

    window.__resilienceChrome = { formatExpiry, describeCluster };
    if (!window.document || !window.document.addEventListener) return;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function connectHref() { return "connect.html" + (window.location.search || ""); }

    function buildChip() {
        const meta = document.querySelector(".topbar__meta");
        if (!meta) return null;
        const chip = el("a", "cluster-chip");
        chip.href = connectHref();
        chip.dataset.state = "none";
        chip.append(el("span", "cluster-chip__dot"), el("span", "", "Checking…"));
        meta.prepend(chip);
        return chip;
    }

    function render(chip, panel, status) {
        const info = describeCluster(status, new Date());
        if (chip) {
            chip.dataset.state = info.state;
            chip.lastChild.textContent = info.chip;
            chip.title = info.detail;
        }
        if (panel) {
            panel.dataset.state = info.state;
            panel.querySelector('[data-role="cluster-panel-title"]').textContent = info.title;
            panel.querySelector('[data-role="cluster-panel-detail"]').textContent = info.detail;
            const link = panel.querySelector('[data-role="cluster-panel-link"]');
            link.textContent = info.action || "";
            link.hidden = !info.action;
            link.href = connectHref();
        }
    }

    async function refresh(chip, panel) {
        let status = null;
        try {
            const response = await fetch("/_extension/cluster");
            if (response.ok) status = await response.json();
        } catch (_error) {
            status = null;
        }
        render(chip, panel, status);
    }

    document.addEventListener("DOMContentLoaded", () => {
        const chip = buildChip();
        const panel = document.querySelector('[data-role="cluster-panel"]');
        refresh(chip, panel);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(chip, panel); });
        // autoconnect.js announces a renewal, a setup or a removal so the status here never goes stale.
        window.addEventListener("resilience-cluster-changed", () => refresh(chip, panel));
    });

    // The approval dialog is a modal: Escape cancels it, like every other Docker Desktop dialog.
    document.addEventListener("keydown", (event) => {
        if (event.key !== "Escape") return;
        const dialog = document.getElementById("approval-modal");
        const cancel = document.getElementById("modal-cancel");
        if (dialog && cancel && dialog.classList.contains("is-visible")) cancel.click();
    });
})();
