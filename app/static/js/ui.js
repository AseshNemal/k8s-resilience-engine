/*
 * Shared UI helpers: state rendering, formatting, raw-JSON panels.
 * SHARED FILE — changes require team review.
 *
 * Formatting only — no research calculations live here.
 */

function setLoading(container, message) {
    if (!container) return;
    container.setAttribute("aria-busy", "true");
    container.innerHTML = `
        <div class="loading-skeleton" role="status" aria-live="polite">
            <span class="is-hidden">${escapeHtml(message || "Loading…")}</span>
            <div class="skeleton-bar skeleton-bar--wide"></div>
            <div class="skeleton-bar skeleton-bar--tall"></div>
            <div class="skeleton-bar"></div>
        </div>
    `;
}

function setEmpty(container, message) {
    if (!container) return;
    container.removeAttribute("aria-busy");
    container.innerHTML = `
        <div class="state empty-state" role="status">
            <p class="state__title">No data available</p>
            <p>${escapeHtml(message || "There is nothing to display yet.")}</p>
        </div>
    `;
}

/**
 * Render an inline error state with a retry button.
 * @param {HTMLElement} container
 * @param {{ endpoint?: string, message?: string, onRetry?: () => void }} options
 */
function setError(container, options = {}) {
    if (!container) return;
    container.removeAttribute("aria-busy");

    const endpointLine = options.endpoint
        ? `<p>Failed endpoint: <code>${escapeHtml(options.endpoint)}</code></p>`
        : "";

    container.innerHTML = `
        <div class="state error-state" role="alert" aria-live="assertive">
            <p class="state__title">Unable to load data</p>
            <p>${escapeHtml(
                options.message ||
                    "Confirm the FastAPI server is running and try again."
            )}</p>
            ${endpointLine}
            <button type="button" class="secondary-button" data-role="retry-button">
                Retry
            </button>
        </div>
    `;

    if (options.onRetry) {
        const button = container.querySelector('[data-role="retry-button"]');
        if (button) {
            button.addEventListener("click", options.onRetry);
        }
    }
}

function setStatusBadge(element, variant, text) {
    if (!element) return;
    // pill-badge (not status-badge) so this keeps its rounded/icon-row
    // styling consistent with the other topbar chips.
    element.className = `pill-badge pill-badge--${variant}`;
    element.textContent = text;
}

function formatPercentage(value, digits = 0) {
    if (value === null || value === undefined || Number.isNaN(value)) {
        return "—";
    }
    return `${Number(value).toFixed(digits)}%`;
}

function formatRatioAsPercentage(ratio, digits = 0) {
    if (ratio === null || ratio === undefined || Number.isNaN(ratio)) {
        return "—";
    }
    return `${(Number(ratio) * 100).toFixed(digits)}%`;
}

/**
 * Turn an UPPER_SNAKE_CASE recommendation/type code into a readable label.
 * Presentation-only formatting; the underlying API value is left untouched.
 */
function formatCodeLabel(code) {
    if (!code) return "—";
    return code
        .toString()
        .split("_")
        .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
        .join(" ");
}

function formatJson(data) {
    return JSON.stringify(data, null, 2);
}

function escapeHtml(value) {
    const div = document.createElement("div");
    div.textContent = value === undefined || value === null ? "" : String(value);
    return div.innerHTML;
}

function updateLastRefreshed(element, date = new Date()) {
    if (!element) return;
    const time = date.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    });
    element.textContent = `Last refreshed ${time}`;
}

/**
 * Wire up a collapsible raw-JSON panel.
 * Expects markup produced by templates/partials/raw_json_panel.html.
 */
function initJsonPanel(panelElement, data) {
    if (!panelElement) return;

    const toggle = panelElement.querySelector('[data-role="json-toggle"]');
    const pre = panelElement.querySelector('[data-role="json-content"]');
    const copyButton = panelElement.querySelector('[data-role="json-copy"]');

    if (pre) {
        pre.textContent = formatJson(data);
    }

    if (toggle) {
        toggle.addEventListener("click", () => {
            const isOpen = panelElement.classList.toggle("is-open");
            toggle.setAttribute("aria-expanded", String(isOpen));
            const label = toggle.querySelector('[data-role="json-toggle-label"]');
            if (label) {
                label.textContent = isOpen ? "Hide Raw Data" : "View Raw Data";
            }
        });
    }

    if (copyButton) {
        copyButton.addEventListener("click", async () => {
            try {
                await navigator.clipboard.writeText(formatJson(data));
                const original = copyButton.textContent;
                copyButton.textContent = "Copied";
                setTimeout(() => {
                    copyButton.textContent = original;
                }, 1500);
            } catch (error) {
                console.error("Copy to clipboard failed:", error);
            }
        });
    }
}

window.ResilienceUi = {
    setLoading,
    setEmpty,
    setError,
    setStatusBadge,
    formatPercentage,
    formatRatioAsPercentage,
    formatCodeLabel,
    formatJson,
    escapeHtml,
    updateLastRefreshed,
    initJsonPanel,
};
