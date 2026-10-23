/*
 * Extension chrome: the cluster-status chip in the header, the Overview connection panel, and
 * Escape-to-close for the approval dialog. Presentation only: it reads the existing
 * GET /_extension/cluster endpoint and changes no data.
 */
(function () {
    "use strict";

    const QUICK_ACTION = "Set up automatic renewal";
    // The one thing a person is ever asked to do about renewal: allow the background job, once.
    const BACKGROUND_ACTION = "Turn on background renewal";

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

    // A credential the helper installed renews itself, so it never shows an expiry or a renew button:
    // an expired one is simply "renewing", with the reason it has not finished yet. Only a problem that
    // a person must fix (access removed or changed in the cluster) asks for their attention.
    function describeManaged(status, automatic, auto) {
        const offered = Boolean(automatic && auto && (auto.background === "off" || auto.background === "stale"));
        const live = "Overview and Deployment topology show your connected cluster; a page without live evidence says so instead of showing figures.";
        if (status.expired) {
            if (!automatic) {
                return { state: "expired", chip: "Credential expired", title: `Credential for ${status.server} has expired`,
                    detail: "Automatic renewal is not available here. Reconnect to read your cluster again. Until then pages show an error instead of data.", action: "Manage connection" };
            }
            if (auto && auto.phase === "attention") {
                return { state: "attention", chip: "Needs your attention", title: "The extension's access needs you",
                    detail: auto.message || "The extension's access in the cluster has to be approved again.", action: "Manage connection" };
            }
            const reason = auto && auto.phase === "retrying" && auto.message ? `${auto.message} ` : "";
            return { state: "renewing", chip: "Renewing…", title: "Renewing the credential automatically",
                detail: `${reason}No action is needed: it renews by itself and pages show data again as soon as that finishes.`,
                action: offered ? BACKGROUND_ACTION : null };
        }
        let how = "The credential renews itself while the extension is open.";
        if (auto && auto.background === "on") how = "The credential renews itself and is kept renewed in the background, even when Docker Desktop is closed.";
        else if (offered) how = "The credential renews itself while the extension is open. Turn on background renewal to keep it renewed when Docker Desktop is closed or the computer sleeps.";
        return { state: "connected", chip: "Connected · full access", title: `Connected to ${status.server}`,
            detail: `Full access. ${how} ${live}`, action: offered ? BACKGROUND_ACTION : "Manage connection" };
    }

    // One source of truth for how a connection state is worded, used by the chip and the Overview panel.
    // `automatic` is true where the host helper can set up self-renewing access (see autoconnect.js);
    // `auto` is the renewal controller's snapshot, when there is one.
    // A credential added by hand never renews itself, so there the wording leads to that setup.
    // Not connected, but the person approved a cluster earlier. Unless they disconnected it, the extension
    // reconnects it by itself (autoconnect.js), so this says what is happening, not "no cluster connected".
    function describeApproved(approved, automatic, auto) {
        const name = approved.context;
        if (approved.disconnectedByUser) {
            return { state: "none", chip: "Not connected", title: `${name} is disconnected`,
                detail: `You disconnected ${name}, so it will not reconnect by itself. Connect it again to approve it. Until then pages show an error instead of data.`,
                action: "Connect cluster" };
        }
        if (!automatic) return null;
        if (auto && auto.phase === "attention") {
            return { state: "attention", chip: "Needs your attention", title: "The extension's access needs you",
                detail: auto.message || "The cluster has to be approved again.", action: "Manage connection" };
        }
        const reason = auto && auto.phase === "retrying" && auto.message ? `${auto.message} ` : "";
        return { state: "renewing", chip: "Reconnecting…", title: `Reconnecting to ${name}`,
            detail: `${reason}You approved this cluster earlier, so it reconnects by itself. No action is needed; pages show data again as soon as that finishes.`,
            action: null };
    }

    function describeCluster(status, now, automatic, auto) {
        if (!status) {
            return { state: "error", chip: "Backend unavailable", title: "The extension backend is not responding",
                detail: "Wait a few seconds and reload. If it persists, restart the extension.", action: null };
        }
        if (!status.connected) {
            if (status.approved) {
                const approved = describeApproved(status.approved, automatic, auto);
                if (approved) return approved;
            }
            return { state: "none", chip: "Not connected", title: "No cluster connected",
                detail: "No cluster is connected, so pages show an error instead of data. Connect a read-only credential to see your own cluster.",
                action: "Connect cluster" };
        }
        if (status.managed) return describeManaged(status, automatic, auto);
        const expiry = formatExpiry(status.expiresAt, now);
        const byHand = !status.managed;
        if (status.expired) {
            let detail = "Renew it to read your cluster again. Until then pages show an error instead of data.";
            let action = "Renew credential";
            if (byHand && automatic) {
                detail = "This credential was added by hand, so it cannot renew itself. Set it up automatically once and it renews itself while the extension is open. Until then pages show an error instead of data.";
                action = "Set up automatic renewal";
            } else if (byHand) {
                action = "Replace credential";
            }
            return { state: "expired", chip: "Credential expired", title: `Credential for ${status.server} has expired`, detail, action };
        }
        const live = "Overview and Deployment topology show your connected cluster; a page without live evidence says so instead of showing figures.";
        const hint = byHand && automatic ? " It was added by hand and will not renew itself." : "";
        return { state: "connected", chip: "Connected · full access", title: `Connected to ${status.server}`,
            detail: `Full access${expiry ? `, credential expires ${expiry}` : ""}.${hint} ${live}`,
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
        const controller = window.__resilienceController;
        const info = describeCluster(status, new Date(), Boolean(support && support.automatic), controller ? controller.snapshot() : null);
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
            link.dataset.background = info.action === BACKGROUND_ACTION ? "true" : "";
            link.dataset.server = (status && status.server) || "";
            link.dataset.context = (status && status.context) || "";
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

    // One click, once: allow the background renewal job. The helper shows the native approval dialog.
    async function enableBackground(chip, panel, link) {
        const controller = window.__resilienceController;
        if (!controller || link.dataset.busy) return;
        link.dataset.busy = "true";
        link.textContent = "Waiting for your approval…";
        await controller.enableBackground(link.dataset.context);
        delete link.dataset.busy;
        await refresh(chip, panel);
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
                if (!window.__resilienceController) return; // plain link to Connect
                if (link.dataset.background === "true") {
                    event.preventDefault();
                    enableBackground(chip, panel, link);
                } else if (link.dataset.quick === "true") {
                    event.preventDefault();
                    quickSetup(chip, panel, link);
                }
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
