// Logic tests for shim.js using a mocked window.ddClient. Run with: node --test extension/ui-src/
// They check the adapter's own behaviour; behaviour of the real Docker Desktop SDK (for example the
// exact error object it throws) can only be confirmed by running the installed extension.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./shim.js", import.meta.url), "utf8");

// Timers that run immediately but record the requested delay, so retry waits are testable.
function immediateTimers() {
    const delays = [];
    return { delays, setTimeout: (fn, ms) => { delays.push(ms); queueMicrotask(fn); return 1; }, clearTimeout: () => {} };
}

function fakeDocument(hrefs) {
    const anchors = hrefs.map((initial) => {
        let href = initial;
        return { getAttribute: () => href, setAttribute: (_name, value) => { href = value; }, closest() { return this; }, get href() { return href; } };
    });
    const listeners = {};
    return {
        anchors,
        addEventListener: (type, fn) => { listeners[type] = fn; },
        querySelectorAll: () => ({ forEach: (fn) => anchors.forEach(fn) }),
        fire: (type, event) => listeners[type](event),
        listeners,
    };
}

function load(requestImpl, { href = "ddui://resilience/index.html", timers = immediateTimers(), document } = {}) {
    const calls = [];
    const nativeCalls = [];
    const window = {
        // A real URL object (has protocol/host like window.location) with a non-http scheme, because the
        // extension UI may not be served over http(s) and url.origin is "null" for such schemes.
        location: new URL(href),
        document,
        fetch: async (input) => {
            nativeCalls.push(input);
            return new Response("native", { status: 200 });
        },
        ddClient: { extension: { vm: { service: { request: async (config) => { calls.push(config); return requestImpl(config); } } } } },
    };
    vm.runInNewContext(source, {
        window, URL, Response, Headers, DOMException, JSON, Promise, Object, String, Number, RegExp, Array,
        setTimeout: timers.setTimeout, clearTimeout: timers.clearTimeout,
    });
    return { window, calls, nativeCalls, timers };
}

test("GET /api/... goes through the SDK and returns a JSON Response", async () => {
    const { window, calls } = load(() => ({ status: "healthy" }));

    const response = await window.fetch("/api/health", { headers: { Accept: "application/json" } });

    assert.equal(response.ok, true);
    assert.deepEqual(await response.json(), { status: "healthy" });
    assert.equal(calls[0].method, "GET");
    assert.equal(calls[0].url, "/api/health");
});

test("query strings are preserved", async () => {
    const { window, calls } = load(() => ({}));

    await window.fetch("/api/deployments/live?namespace=demo");

    assert.equal(calls[0].url, "/api/deployments/live?namespace=demo");
});

test("POST with a JSON string body is sent as an object with the method", async () => {
    const { window, calls } = load(() => ({ status: "PENDING_APPROVAL" }));

    const response = await window.fetch("/api/recommendations/rec-001/request-approval", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ recommendationId: "rec-001", proposalVersion: "v1" }),
    });

    assert.deepEqual(calls[0].data, { recommendationId: "rec-001", proposalVersion: "v1" });
    assert.equal(calls[0].method, "POST");
    assert.equal((await response.json()).status, "PENDING_APPROVAL");
});

test("PUT and DELETE to /_extension/ are routed to the backend", async () => {
    const { window, calls } = load(() => ({ connected: false }));

    await window.fetch("/_extension/cluster", { method: "PUT", body: JSON.stringify({ kubeconfig: "x" }) });
    await window.fetch("/_extension/cluster", { method: "DELETE" });

    assert.deepEqual(calls.map((c) => c.method), ["PUT", "DELETE"]);
});

test("an SDK error with a status code becomes a Response with that status and a JSON detail", async () => {
    const { window } = load(() => { throw { statusCode: 404, message: '{"detail":"Approval not found"}' }; });

    const response = await window.fetch("/api/recommendations/x/approval");

    assert.equal(response.ok, false);
    assert.equal(response.status, 404);
    assert.deepEqual(await response.json(), { detail: "Approval not found" });
});

test("a plain-text error message is wrapped as a JSON detail", async () => {
    const { window } = load(() => { throw { statusCode: 409, message: "Approval already pending" }; });

    const response = await window.fetch("/api/x", { method: "POST" });

    assert.equal(response.status, 409);
    assert.deepEqual(await response.json(), { detail: "Approval already pending" });
});

test("an error without a status code becomes a 502", async () => {
    const { window } = load(() => { throw new Error("socket closed"); });

    const response = await window.fetch("/api/health");

    assert.equal(response.status, 502);
    assert.match((await response.json()).detail, /socket closed/);
});

test("a string result is returned as the response body", async () => {
    const { window } = load(() => "plain text");

    assert.equal(await (await window.fetch("/api/text")).text(), "plain text");
});

test("requests that are not for the backend use the browser's fetch", async () => {
    const { window, calls, nativeCalls } = load(() => ({}));

    await window.fetch("https://example.com/data.json");
    await window.fetch("static/css/layout.css");
    await window.fetch("/static/img/logo.png");

    assert.equal(calls.length, 0);
    assert.equal(nativeCalls.length, 3);
});

test("an aborted request rejects with AbortError and a pre-aborted signal never calls the backend", async () => {
    const pending = new Promise(() => {});
    const { window, calls } = load(() => pending);
    const controller = new AbortController();

    const inFlight = window.fetch("/api/slow", { signal: controller.signal });
    controller.abort();
    await assert.rejects(inFlight, { name: "AbortError" });

    const before = calls.length;
    await assert.rejects(window.fetch("/api/slow", { signal: controller.signal }), { name: "AbortError" });
    assert.equal(calls.length, before);
});

test("outside Docker Desktop (no window.ddClient) fetch is left untouched", () => {
    const window = { location: new URL("http://localhost/"), fetch: () => "native" };
    vm.runInNewContext(source, { window, URL, Response, Headers, DOMException, JSON, Promise, Object, String, Number });

    assert.equal(window.fetch(), "native");
});

// --- startup race: the tab can load before the backend is listening ------------------------------

const NOT_READY = { name: "Error", message: "other side closed" };

test("a GET is retried while the backend is still starting, then succeeds", async () => {
    let failures = 2;
    const { window, calls, timers } = load(() => { if (failures-- > 0) throw NOT_READY; return { status: "healthy" }; });

    const response = await window.fetch("/api/health");

    assert.equal(response.status, 200);
    assert.deepEqual(await response.json(), { status: "healthy" });
    assert.equal(calls.length, 3);
    assert.deepEqual(timers.delays, [400, 800]);
});

test("retries stop after five waits (about 6 s) and report a 502", async () => {
    const { window, calls, timers } = load(() => { throw NOT_READY; });

    const response = await window.fetch("/api/health");

    assert.equal(response.status, 502);
    assert.equal(calls.length, 6);
    assert.deepEqual(timers.delays, [400, 800, 1200, 1600, 2000]);
    assert.match((await response.json()).detail, /other side closed/);
});

test("writes are never retried", async () => {
    for (const method of ["POST", "PUT", "DELETE", "PATCH"]) {
        const { window, calls } = load(() => { throw NOT_READY; });

        const response = await window.fetch("/api/x", { method, body: "{}" });

        assert.equal(response.status, 502, method);
        assert.equal(calls.length, 1, method);
    }
});

test("a real HTTP error from the backend is passed through, not retried", async () => {
    const { window, calls } = load(() => { throw { statusCode: 404, message: '{"detail":"Not Found"}' }; });

    const response = await window.fetch("/api/nope");

    assert.equal(response.status, 404);
    assert.equal(calls.length, 1);
});

test("an error with no status that does not look like a connection failure is not retried", async () => {
    const { window, calls } = load(() => { throw { message: "Unexpected response shape" }; });

    const response = await window.fetch("/api/x");

    assert.equal(response.status, 502);
    assert.equal(calls.length, 1);
});

test("aborting while waiting to retry rejects with AbortError and stops retrying", async () => {
    const timers = { setTimeout: () => 1, clearTimeout: () => {} }; // the wait never ends by itself
    const { window, calls } = load(() => { throw NOT_READY; }, { timers });
    const controller = new AbortController();

    const pending = window.fetch("/api/slow", { signal: controller.signal });
    await new Promise((resolve) => setImmediate(resolve));
    controller.abort();

    await assert.rejects(pending, { name: "AbortError" });
    assert.equal(calls.length, 1);
});

// --- navigation: Docker Desktop only injects the SDK into pages loaded with its query string -----

const ENTRY = "ddui://resilience/index.html?platform=darwin&hasBackend=true&backendSocket=ext-sockets%2Fx.sock";

test("links to other extension pages carry the entry query string, once", () => {
    const document = fakeDocument(["workflows.html", "index.html", "https://example.com/y.html", "/api/x", "static/css/a.css", "#top"]);
    load(() => ({}), { href: ENTRY, document });

    document.fire("DOMContentLoaded");
    document.fire("DOMContentLoaded"); // idempotent

    const search = new URL(ENTRY).search;
    assert.deepEqual(document.anchors.map((a) => a.href), [
        "workflows.html" + search, "index.html" + search, "https://example.com/y.html", "/api/x", "static/css/a.css", "#top",
    ]);
});

test("a link added after load is fixed up when it is clicked", () => {
    const document = fakeDocument([]);
    load(() => ({}), { href: ENTRY, document });
    const late = fakeDocument(["recommendations.html"]).anchors[0];

    document.fire("click", { target: late });

    assert.equal(late.href, "recommendations.html" + new URL(ENTRY).search);
});

test("without an entry query string nothing is rewritten", () => {
    const document = fakeDocument(["workflows.html"]);
    load(() => ({}), { document });

    assert.deepEqual(Object.keys(document.listeners), []); // nothing to carry, so nothing is registered
    assert.equal(document.anchors[0].href, "workflows.html");
});

// --- error bodies: the SDK drops them, so the backend sends errors inside a 200 envelope ---------

test("every backend request asks for the error envelope", async () => {
    const { window, calls } = load(() => ({}));

    await window.fetch("/api/health");
    await window.fetch("/api/x", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });

    assert.equal(calls[0].headers["X-Resilience-Envelope"], "1");
    assert.equal(calls[1].headers["X-Resilience-Envelope"], "1");
    assert.equal(calls[1].headers["Content-Type"], "application/json");
});

test("an enveloped error becomes a Response with the real status and the backend's own body", async () => {
    const envelope = { __resilienceError: true, status: 422, body: { detail: "This credential can change the cluster." } };
    const { window } = load(() => envelope);

    const response = await window.fetch("/_extension/cluster", { method: "PUT", body: "{}" });

    assert.equal(response.status, 422);
    assert.equal(response.ok, false);
    assert.deepEqual(await response.json(), { detail: "This credential can change the cluster." });
});

test("an enveloped error with a plain-text body is wrapped as a JSON detail", async () => {
    const { window } = load(() => ({ __resilienceError: true, status: 500, body: "Internal Server Error" }));

    const response = await window.fetch("/api/x");

    assert.equal(response.status, 500);
    assert.deepEqual(await response.json(), { detail: "Internal Server Error" });
});

test("an enveloped error is not retried", async () => {
    const { window, calls } = load(() => ({ __resilienceError: true, status: 404, body: { detail: "Not Found" } }));

    await window.fetch("/api/nope");

    assert.equal(calls.length, 1);
});

test("a normal object that merely resembles an envelope is passed through", async () => {
    const { window } = load(() => ({ status: 404, body: "x" }));

    const response = await window.fetch("/api/x");

    assert.equal(response.status, 200);
});
