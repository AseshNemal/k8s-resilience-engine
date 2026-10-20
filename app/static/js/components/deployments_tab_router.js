/**
 * Phase 5 Dynamic Tab Router: Manages multi-tab switching with URL hash state.
 * Component: Component 2 - Deployment Topology Modeling
 */
window.DeploymentsTabRouter = (function () {
    "use strict";

    const VALID_TABS = new Set(["topology", "intent", "drift", "scenarios"]);

    function init() {
        const tabButtons = document.querySelectorAll(".tab-nav-btn");
        if (!tabButtons.length) return;

        tabButtons.forEach((btn) => {
            btn.addEventListener("click", function () {
                const targetId = this.dataset.target;
                if (targetId) {
                    const tabKey = targetId.replace("tab-", "");
                    switchTab(tabKey);
                }
            });
        });

        // Initialize from URL hash or default to topology
        const hash = window.location.hash.replace("#", "");
        if (VALID_TABS.has(hash)) {
            switchTab(hash);
        } else {
            switchTab("topology");
        }

        window.addEventListener("hashchange", function () {
            const newHash = window.location.hash.replace("#", "");
            if (VALID_TABS.has(newHash)) {
                switchTab(newHash, false);
            }
        });
    }

    function switchTab(tabKey, updateHash = true) {
        const targetId = `tab-${tabKey}`;
        const targetPane = document.getElementById(targetId);
        const targetBtn = document.getElementById(`tab-btn-${tabKey}`);

        if (!targetPane || !targetBtn) return;

        // Deactivate all tabs
        document.querySelectorAll(".tab-nav-btn").forEach((btn) => {
            btn.classList.remove("active");
            btn.setAttribute("aria-selected", "false");
        });

        document.querySelectorAll(".tab-pane").forEach((pane) => {
            pane.classList.remove("active");
            pane.setAttribute("hidden", "true");
        });

        // Activate target
        targetBtn.classList.add("active");
        targetBtn.setAttribute("aria-selected", "true");
        targetPane.removeAttribute("hidden");
        // Add the class on the next frame so the fade-in animation restarts.
        requestAnimationFrame(() => targetPane.classList.add("active"));

        if (updateHash) {
            history.replaceState(null, "", `#${tabKey}`);
        }
    }

    return {
        init: init,
        switchTab: switchTab,
    };
})();
