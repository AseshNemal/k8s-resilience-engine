/*
 * Application shell behaviour: mobile sidebar drawer, active nav, health badge.
 * SHARED FILE — changes require team review.
 *
 * Migrated from the old dashboard.js: the /api/health status-badge check
 * now runs on every page (topbar "Environment" area) instead of only the
 * dashboard.
 */

function initSidebarToggle() {
    const toggle = document.querySelector('[data-role="nav-toggle"]');
    const sidebar = document.querySelector('[data-role="sidebar"]');
    const backdrop = document.querySelector('[data-role="sidebar-backdrop"]');

    if (!toggle || !sidebar) return;

    const closeSidebar = () => {
        sidebar.classList.remove("is-open");
        if (backdrop) backdrop.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
    };

    const openSidebar = () => {
        sidebar.classList.add("is-open");
        if (backdrop) backdrop.classList.add("is-open");
        toggle.setAttribute("aria-expanded", "true");
    };

    toggle.addEventListener("click", () => {
        const isOpen = sidebar.classList.contains("is-open");
        if (isOpen) {
            closeSidebar();
        } else {
            openSidebar();
        }
    });

    if (backdrop) {
        backdrop.addEventListener("click", closeSidebar);
    }

    document.addEventListener("keydown", (event) => {
        if (event.key === "Escape") {
            closeSidebar();
        }
    });
}

const SIDEBAR_COLLAPSE_STORAGE_KEY = "resilience-engine:sidebar-collapsed";

function initSidebarCollapse() {
    const toggle = document.querySelector('[data-role="sidebar-collapse-toggle"]');
    const label = document.querySelector('[data-role="sidebar-collapse-label"]');
    if (!toggle) return;

    const applyState = (collapsed) => {
        document.body.classList.toggle("sidebar-collapsed", collapsed);
        toggle.setAttribute("aria-expanded", String(!collapsed));
        if (label) label.textContent = collapsed ? "Expand" : "Collapse";
    };

    let storedState = null;
    try {
        storedState = window.localStorage.getItem(SIDEBAR_COLLAPSE_STORAGE_KEY);
    } catch (error) {
        // localStorage unavailable (private mode, etc.) — default to expanded.
    }
    applyState(storedState === "true");

    toggle.addEventListener("click", () => {
        const collapsed = !document.body.classList.contains("sidebar-collapsed");
        applyState(collapsed);
        try {
            window.localStorage.setItem(
                SIDEBAR_COLLAPSE_STORAGE_KEY,
                String(collapsed)
            );
        } catch (error) {
            // Ignore persistence failures — the toggle still works this session.
        }
    });
}

async function initHealthStatus() {
    const badge = document.querySelector('[data-role="health-badge"]');
    if (!badge || !window.ResilienceApi) return;

    try {
        const data = await window.ResilienceApi.fetchJson("/api/health");
        window.ResilienceUi.setStatusBadge(
            badge,
            "success",
            data.status === "healthy" ? "System Healthy" : data.status
        );
    } catch (error) {
        window.ResilienceUi.setStatusBadge(badge, "danger", "System Unavailable");
        console.error("Health check failed:", error);
    }
}

document.addEventListener("DOMContentLoaded", () => {
    initSidebarToggle();
    initSidebarCollapse();
    initHealthStatus();
});
