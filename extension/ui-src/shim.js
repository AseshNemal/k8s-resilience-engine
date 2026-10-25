/*
 * fetch() adapter for the Docker Desktop extension UI.
 *
 * The dashboard pages call fetch("/api/...") expecting a web server. Inside Docker Desktop there
 * is no HTTP server: the backend listens on a Unix socket and the SDK carries requests to it.
 * This script sends same-origin /api/ and /_extension/ requests through
 * ddClient.extension.vm.service and returns a standard Response, so the existing pages and
 * their scripts run unchanged. Every other request goes to the browser's real fetch.
 *
 * It uses the SDK object Docker Desktop injects into extension pages (window.ddClient), the same
 * way Docker's own vm-service sample does, so the extension needs no npm packages or bundler.
 *
 * Two behaviours of Docker Desktop shape this file (both found by running the installed extension):
 *  - window.ddClient is injected only into a page loaded with the query string Docker Desktop adds
 *    to the entry URL (?hasBackend=true&backendSocket=...&extensionName=...). A page opened without
 *    it has no SDK at all, so links between pages must carry that query string along.
 *  - The SDK throws a generic "HTTP 422: Unprocessable Entity" for any non-2xx answer and discards
 *    the response body. The adapter therefore asks the backend (header below) to return errors
 *    inside a 200 envelope (extension/error_envelope.py) and restores the real status and body.
 *  - The tab can load before the backend has finished starting (install, update, restart), and the
 *    SDK then fails with a transport error such as "other side closed". Idempotent reads are
 *    retried briefly; writes never are.
 */
(function () {
const BACKEND_PREFIXES = ["/api/", "/_extension/"];
// Total wait of about 6 s, inside the pages' own 8 s request timeout (app/static/js/api.js).
const RETRY_DELAYS_MS = [400, 800, 1200, 1600, 2000];
const ENVELOPE_HEADER = "X-Resilience-Envelope";
const TRANSPORT_FAILURE = /other side closed|ECONNREFUSED|ECONNRESET|ENOENT|EPIPE|socket hang up|fetch failed|connect/i;

function backendPath(input) {
    const raw = typeof input === "string" ? input : input && input.url;
    if (typeof raw !== "string") return null;
    let url;
    try {
        url = new URL(raw, window.location.href);
    } catch (_error) {
        return null;
    }
    // Compare protocol and host, not url.origin: origin is the string "null" for non-http(s)
    // schemes, which would silently send every API call to the browser's own fetch.
    if (raw.startsWith("//") || url.protocol !== window.location.protocol || url.host !== window.location.host) {
        return null;
    }
    // On Windows Docker Desktop loads the page from file:///C:/..., and the URL parser keeps the drive
    // letter when it resolves "/api/..." against it (file:///C:/api/...). Drop it so the backend path is
    // the same as on macOS and Linux; otherwise every backend call falls through to the browser's fetch.
    const pathname = url.protocol === "file:" ? url.pathname.replace(/^\/[A-Za-z]:(?=\/)/, "") : url.pathname;
    if (!BACKEND_PREFIXES.some((prefix) => pathname.startsWith(prefix))) return null;
    return pathname + url.search;
}

function jsonResponse(body, status) {
    const code = status >= 200 && status <= 599 ? status : 502;
    return new Response(body, { status: code, headers: { "Content-Type": "application/json" } });
}

function asDetail(message) {
    try {
        JSON.parse(message);
        return message;
    } catch (_error) {
        return JSON.stringify({ detail: message });
    }
}

// Turn the SDK's result into a Response, restoring the real status and body of an enveloped error.
function unwrap(result) {
    if (result && typeof result === "object" && result.__resilienceError === true) {
        const body = typeof result.body === "string" ? JSON.stringify({ detail: result.body }) : JSON.stringify(result.body);
        return jsonResponse(body, Number(result.status) || 502);
    }
    return jsonResponse(typeof result === "string" ? result : JSON.stringify(result), 200);
}

function statusOf(error) {
    return Number(error && (error.statusCode || error.status)) || 0;
}

// True only for "the request never got an HTTP answer" failures, never for a real 4xx/5xx.
function isTransportFailure(error) {
    const status = statusOf(error);
    const message = String((error && (error.message || error.stderr)) || error || "");
    return (status === 0 || status === 502 || status === 503 || status === 504) && TRANSPORT_FAILURE.test(message);
}

function abortError() {
    return new DOMException("The operation was aborted.", "AbortError");
}

function sleep(ms, signal) {
    return new Promise((resolve, reject) => {
        if (signal && signal.aborted) return reject(abortError());
        const timer = setTimeout(resolve, ms);
        if (signal) {
            signal.addEventListener("abort", () => { clearTimeout(timer); reject(abortError()); }, { once: true });
        }
    });
}

async function requestWithRetry(service, config, signal) {
    const retryable = config.method === "GET" || config.method === "HEAD";
    for (let attempt = 0; ; attempt++) {
        try {
            return await service.request(config);
        } catch (error) {
            if (!retryable || attempt >= RETRY_DELAYS_MS.length || !isTransportFailure(error)) throw error;
            await sleep(RETRY_DELAYS_MS[attempt], signal);
        }
    }
}

// Keep Docker Desktop's entry-URL query string on links between the extension's own pages.
function carryQueryString() {
    const search = window.location.search;
    if (!search || !window.document) return;
    const withSearch = (anchor) => {
        const href = anchor.getAttribute("href");
        if (href && /^[A-Za-z0-9_-]+\.html$/.test(href)) anchor.setAttribute("href", href + search);
    };
    window.document.addEventListener("DOMContentLoaded", () => window.document.querySelectorAll("a[href]").forEach(withSearch));
    window.document.addEventListener("click", (event) => {
        const anchor = event.target && event.target.closest && event.target.closest("a[href]");
        if (anchor) withSearch(anchor);
    }, true);
}

// Docker may recreate the view at index.html when returning to the extension. Remember only
// a known local page name, and always use the current SDK query string when restoring it.
function restoreNavigation() {
    if (!window.ddClient || !window.document || !window.location.search) return;
    const pages = ["index.html", "workflows.html", "deployments.html", "failure-impacts.html", "recommendations.html", "connect.html"];
    const key = "resilience.extension.page.v1";
    const current = window.location.pathname.split("/").pop();
    if (!pages.includes(current)) return;
    try {
        const saved = window.localStorage.getItem(key);
        if (current === "index.html" && pages.includes(saved) && saved !== current) {
            window.location.replace(saved + window.location.search);
            return;
        }
        window.localStorage.setItem(key, current);
    } catch (_error) { /* Storage may be disabled by the host. */ }
    window.document.addEventListener("click", (event) => {
        if (event.defaultPrevented || event.button > 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        const anchor = event.target && event.target.closest && event.target.closest("a[href]");
        if (!anchor || anchor.getAttribute("target") === "_blank" || anchor.hasAttribute?.("download")) return;
        const href = anchor.getAttribute("href") || "";
        const page = href.split("?")[0];
        // Recording the destination lets an explicit click on Overview stay on Overview.
        if (pages.includes(page)) {
            try { window.localStorage.setItem(key, page); } catch (_error) { /* Storage is optional. */ }
        }
    });
}

function installFetchAdapter() {
    const service = window.ddClient && window.ddClient.extension && window.ddClient.extension.vm
        ? window.ddClient.extension.vm.service
        : null;
    if (!service) return; // not running inside Docker Desktop: leave fetch alone

    const nativeFetch = window.fetch.bind(window);

    window.fetch = async function (input, init = {}) {
        const path = backendPath(input);
        if (path === null) return nativeFetch(input, init);

        const method = (init.method || (input && input.method) || "GET").toUpperCase();
        const headers = Object.assign({}, init.headers instanceof Headers ? Object.fromEntries(init.headers) : init.headers);
        headers[ENVELOPE_HEADER] = "1";
        let data;
        if (typeof init.body === "string") {
            try {
                data = JSON.parse(init.body);
            } catch (_error) {
                data = init.body;
            }
        }

        const signal = init.signal;
        if (signal && signal.aborted) throw abortError();

        const call = requestWithRetry(service, { url: path, method, headers, data }, signal)
            .then((result) => unwrap(result))
            .catch((error) => {
                if (error && error.name === "AbortError") throw error;
                const message = String((error && (error.message || error.stderr)) || "Backend request failed");
                return jsonResponse(asDetail(message), statusOf(error) || 502);
            });

        if (!signal) return call;
        return Promise.race([
            call,
            new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(abortError()), { once: true })),
        ]);
    };
    window.__resilienceFetchAdapter = true;
}

carryQueryString();
restoreNavigation();
installFetchAdapter();
})();
