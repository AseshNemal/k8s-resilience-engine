/*
 * Extension chrome: the cluster-status chip in the header, the Overview connection panel, and
 * Escape-to-close for the approval dialog. Presentation only: it reads the existing
 * GET /_extension/cluster endpoint and changes no data.
 */
(function () {
    "use strict";

    const QUICK_ACTION = "Set up automatic renewal";

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
    // `automatic` is true where the host helper can set up self-renewing access (see autoconnect.js).
    // A credential added by hand never renews itself, so there the wording leads to that setup.
    function describeCluster(status, now, automatic) {
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
        const byHand = !status.managed;
        if (status.expired) {
            let detail = "Renew it to read your cluster again. Results on this page are sample data either way.";
            let action = "Renew credential";
            if (byHand && automatic) {
                detail = "This credential was added by hand, so it cannot renew itself. Set it up automatically once and it renews itself while the extension is open. Results on this page are sample data either way.";
                action = "Set up automatic renewal";
            } else if (byHand) {
                action = "Replace credential";
            }
            return { state: "expired", chip: "Credential expired", title: `Credential for ${status.server} has expired`, detail, action };
        }
        const live = "Overview and the research pages show sample data; Deployment topology can show your live cluster.";
        const hint = byHand && automatic ? " It was added by hand and will not renew itself." : "";
        return { state: "connected", chip: "Connected · read-only", title: `Connected to ${status.server}`,
            detail: `Read-only access${expiry ? `, credential expires ${expiry}` : ""}.${hint} ${live}`,
            action: byHand && automatic ? "Set up automatic renewal" : "Manage connection" };
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
        const support = window.__resilienceAutoSupport;
        const info = describeCluster(status, new Date(), Boolean(support && support.automatic));
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
            // The automatic-setup button does the setup in place; the href stays as the fallback.
            link.dataset.quick = info.action === QUICK_ACTION ? "true" : "";
            link.dataset.server = (status && status.server) || "";
        }
    }

    // One click on the banner: set up automatic renewal for the cluster the credential points at.
    // The macOS approval dialog still appears; this only removes the hop to the Connect page.
    async function quickSetup(chip, panel, link) {
        const controller = window.__resilienceController;
        if (!controller || link.dataset.busy) return false;
        link.dataset.busy = "true";
        link.textContent = "Waiting for your approval…";
        const result = await controller.quickSetup(link.dataset.server);
        delete link.dataset.busy;
        await refresh(chip, panel);
        if (result.ok) return true;
        // "choose" means several clusters matched: that decision belongs on the Connect page.
        if (result.state === "choose") { window.location.href = connectHref(); return true; }
        const detail = panel && panel.querySelector('[data-role="cluster-panel-detail"]');
        if (detail && window.__resilienceAuto) detail.textContent = window.__resilienceAuto.messageFor(result.state);
        return true;
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
        const link = panel && panel.querySelector('[data-role="cluster-panel-link"]');
        if (link) {
            link.addEventListener("click", (event) => {
                if (link.dataset.quick !== "true" || !window.__resilienceController) return; // plain link to Connect
                event.preventDefault();
                quickSetup(chip, panel, link);
            });
        }
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
