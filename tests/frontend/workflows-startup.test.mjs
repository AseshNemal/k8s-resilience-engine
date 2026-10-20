// The Workflows page must never be left on "Loading…" when it starts. The page script runs against a fake page and
// a stubbed network; the guided form's trace-service address is a required field, as in the real page, so an
// empty address makes reportValidity() fail. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../../app/static/js/components/workflows.js", import.meta.url), "utf8");

function makeEnvironment({ status, discover }) {
    const registry = new Map();
    const element = (key) => {
        if (registry.has(key)) return registry.get(key);
        const node = {
            key, children: [], attributes: {}, className: "", textContent: "", style: {}, listeners: {}, dataset: {},
            disabled: false, hidden: false, value: "", checked: false,
            classList: { add() {}, remove() {}, toggle() { return false; }, contains() { return false; } },
            setAttribute(name, value) { this.attributes[name] = value; },
            removeAttribute(name) { delete this.attributes[name]; },
            getAttribute: () => null,
            append(...nodes) { this.children.push(...nodes); },
            appendChild(child) { this.children.push(child); return child; },
            replaceChildren(...nodes) { this.children = [...nodes]; },
            addEventListener(type, fn) { this.listeners[type] = fn; },
            removeEventListener() {},
            querySelector: (selector) => element(selector),
            querySelectorAll: () => [],
            closest: () => null,
            focus() {},
            scrollIntoView() {},
        };
        registry.set(key, node);
        return node;
    };

    // The guided form's required address field, as in the real page.
    const guidedAddress = element("#guided-jaeger-url");
    const guidedForm = element('[data-role="guided-analysis-form"]');
    guidedForm.reportValidity = () => guidedAddress.value.trim() !== "";
    guidedForm.fields = () => ({ environmentId: "local", evidenceTier: "demo", jaegerUrl: guidedAddress.value,
        entryService: "frontend", lookback: "1h" });

    const listeners = {};
    const document = {
        querySelector: (selector) => element(selector),
        querySelectorAll: () => [],
        getElementById: (id) => element(`#${id}`),
        createElement: (tag) => ({ ...element(`new:${tag}:${Math.random()}`), tag }),
        createTextNode: (text) => ({ textContent: text }),
        addEventListener: (type, fn) => { listeners[type] = fn; },
    };

    const calls = [];
    const json = (status, body) => ({ ok: status < 400, status, json: async () => body, headers: { get: () => null } });
    const fetch = async (url, init = {}) => {
        const method = init.method || "GET";
        calls.push([method, url, init.body]);
        if (url.startsWith("/api/workflows/jaeger/status")) return status();
        if (url === "/api/workflows/analysis-sessions/discover") return discover();
        return json(200, []);
    };
    class FormData {
        constructor(form) { this.values = form.fields(); }
        get(name) { return this.values[name] ?? null; }
    }

    const window = {
        ResilienceApi: { fetchJson: async (url) => {
            const response = await fetch(url);
            if (!response.ok) throw Object.assign(new Error(`HTTP ${response.status}`), { status: response.status });
            return response.json();
        } },
        ResilienceUi: { setError() {}, formatRatioAsPercentage: (r) => String(r), initJsonPanel() {}, updateLastRefreshed() {} },
    };
    const sandbox = { window, document, fetch, FormData, console, setTimeout, clearTimeout, URLSearchParams, AbortController };
    vm.createContext(sandbox);
    vm.runInContext(script, sandbox, { filename: "workflows.js" });
    return { element, listeners, calls, json, guidedAddress };
}

const operation = (name) => ({ operationName: name, traceCount: 3, participatingServices: ["a", "b"] });

test("the address the server uses is filled in before workflows are discovered", async () => {
    const env = makeEnvironment({
        status: () => env.json(200, { healthy: true, jaegerUrl: "http://host.docker.internal:16686" }),
        discover: () => env.json(200, [operation("POST /cart/checkout")]),
    });

    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.equal(env.guidedAddress.value, "http://host.docker.internal:16686");
    const discovery = env.calls.find(([method, url]) => method === "POST" && url === "/api/workflows/analysis-sessions/discover");
    assert.ok(discovery, "discovery was requested once the address was known");
    assert.equal(JSON.parse(discovery[2]).jaegerUrl, "http://host.docker.internal:16686");
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
});

test("when the server cannot say which address to use the page says so instead of loading forever", async () => {
    const env = makeEnvironment({
        status: () => env.json(500, {}),
        discover: () => { throw new Error("discovery must not run without an address"); },
    });

    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const options = env.element('[data-role="guided-operation"]').children.map((child) => child.textContent);
    assert.deepEqual(options, ["Enter the trace service address to load workflows"]);
    assert.ok(env.element('[data-role="guided-analysis-status"]').textContent.includes("trace service address"));
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
    assert.ok(!env.calls.some(([, url]) => url === "/api/workflows/analysis-sessions/discover"));
});

test("an unreachable trace service ends the loading state with a clear message", async () => {
    const env = makeEnvironment({
        status: () => env.json(200, { healthy: false, jaegerUrl: "http://host.docker.internal:16686" }),
        discover: () => env.json(503, { detail: "Jaeger server unreachable" }),
    });

    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const options = env.element('[data-role="guided-operation"]').children.map((child) => child.textContent);
    assert.deepEqual(options, ["No workflows available — check advanced settings"]);
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
});
