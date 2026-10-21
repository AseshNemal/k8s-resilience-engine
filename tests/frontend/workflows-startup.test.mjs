// The Workflows page must never be left on "Loading…" when it starts, and must find observed workflows by itself.
// The page script runs against a fake page, a fake clock and a stubbed network; the guided form's trace-service
// address is a required field, as in the real page, so an empty address makes reportValidity() fail.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../../app/static/js/components/workflows.js", import.meta.url), "utf8");
const pollerScript = readFileSync(new URL("../../app/static/js/workflow-discovery-poller.js", import.meta.url), "utf8");

const flush = () => new Promise((resolve) => setImmediate(resolve));
const DISCOVERY = "/api/workflows/discovery";

function makeEnvironment({ status, discover, hidden = false }) {
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
    guidedForm.fields = () => ({ environmentId: "local", evidenceTier: "experimental", jaegerUrl: guidedAddress.value,
        lookback: element("#guided-lookback").value || "1h" });

    const listeners = {};
    const document = {
        hidden,
        querySelector: (selector) => element(selector),
        querySelectorAll: () => [],
        getElementById: (id) => element(`#${id}`),
        createElement: (tag) => ({ ...element(`new:${tag}:${Math.random()}`), tag }),
        createTextNode: (text) => ({ textContent: text }),
        addEventListener: (type, fn) => { listeners[type] = fn; },
    };

    const calls = [];
    const json = (code, body) => ({ ok: code < 400, status: code, json: async () => body, headers: { get: () => null } });
    const fetch = async (url, init = {}) => {
        const method = init.method || "GET";
        calls.push([method, url, init.body]);
        if (url.startsWith("/api/workflows/jaeger/status")) return status();
        if (url === DISCOVERY) return discover();
        return json(200, []);
    };
    class FormData {
        constructor(form) { this.values = form.fields(); }
        get(name) { return this.values[name] ?? null; }
    }

    // A fake clock: timers run only when a test advances it.
    const timers = [];
    let clock = 0;
    const setTimeout = (fn, ms) => { const timer = { fn, at: clock + ms }; timers.push(timer); return timer; };
    const clearTimeout = (timer) => { const at = timers.indexOf(timer); if (at >= 0) timers.splice(at, 1); };
    const advance = async (ms) => {
        const end = clock + ms;
        for (;;) {
            const due = timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at)[0];
            if (!due) break;
            timers.splice(timers.indexOf(due), 1);
            clock = due.at;
            due.fn();
            await flush();
        }
        clock = end;
    };

    const windowListeners = {};
    const window = {
        addEventListener: (type, fn) => { windowListeners[type] = fn; },
        ResilienceApi: { fetchJson: async (url) => {
            const response = await fetch(url);
            if (!response.ok) {
                // As app/static/js/api.js: a structured {code, message} detail becomes the error's message.
                const detail = (await response.json().catch(() => ({}))).detail;
                const structured = detail && typeof detail === "object" && typeof detail.code === "string";
                throw Object.assign(new Error(structured ? detail.message : `Request failed with HTTP ${response.status}`),
                    { status: response.status });
            }
            return response.json();
        } },
        ResilienceUi: { setError() {}, formatRatioAsPercentage: (r) => String(r), initJsonPanel() {}, updateLastRefreshed() {} },
    };
    // The poller's focus rate limit reads the clock, so the page's Date follows the same fake time.
    class FakeDate extends Date { static now() { return clock; } }
    const sandbox = { window, document, fetch, FormData, console, setTimeout, clearTimeout, URLSearchParams, AbortController,
        Date: FakeDate, JSON, Math };
    vm.createContext(sandbox);
    vm.runInContext(pollerScript, sandbox, { filename: "workflow-discovery-poller.js" });
    vm.runInContext(script, sandbox, { filename: "workflows.js" });
    return { element, listeners, windowListeners, document, calls, json, guidedAddress, advance, timers };
}

const options = (env) => env.element('[data-role="guided-operation"]').children.map((child) => child.textContent);
const observed = (env) => env.element('[data-role="guided-observed"]');
const stateLabel = (env) => env.element('[data-role="guided-observed-state"]').textContent;
const discoveryCalls = (env) => env.calls.filter(([method, url]) => method === "POST" && url === DISCOVERY);
const healthCalls = (env) => env.calls.filter(([, url]) => url.startsWith("/api/workflows/jaeger/status"));
const ADDRESS = "http://host.docker.internal:16686";
const healthy = (env) => env.json(200, { healthy: true, jaegerUrl: ADDRESS });
const down = (env) => env.json(200, { healthy: false, jaegerUrl: ADDRESS });

const workflow = (operationName, extra = {}) => ({
    operationName, workflowId: operationName, rootService: "frontend", traceCount: 3,
    participatingServices: ["frontend", "backend"], sampleTraceIds: ["t"], status: "candidate",
    evidenceStatus: "complete", traceQuality: { validCount: 3, partialCount: 0, invalidCount: 0 }, ...extra,
});
const report = (state, workflows = [], extra = {}) => ({
    state, message: `message for ${state}`, retryable: state !== "discovered", jaegerUrl: ADDRESS, lookback: "1h",
    limits: { maxServices: 12, tracesPerService: 50, maxTraces: 500 },
    servicesRecorded: ["backend", "frontend"], servicesScanned: ["backend", "frontend"], servicesSkipped: [],
    servicesUnexamined: [], servicesCalledByOthers: [], entryDetection: "scan_all",
    tracesFetched: workflows.length * 3, tracesWithCompleteEvidence: workflows.length * 3, truncated: false,
    workflows, ...extra,
});

async function open(env) {
    await env.listeners.DOMContentLoaded();
    await flush();
}

test("the address the server uses is filled in before workflows are discovered", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [workflow("POST /cart/checkout")])),
    });

    await open(env);

    assert.equal(env.guidedAddress.value, ADDRESS);
    assert.ok(discoveryCalls(env).length === 1, "discovery was requested once the address was known");
    assert.equal(JSON.parse(discoveryCalls(env)[0][2]).jaegerUrl, ADDRESS);
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
});

test("when the server cannot say which address to use the page says so instead of loading forever", async () => {
    const env = makeEnvironment({
        status: () => env.json(500, {}),
        discover: () => { throw new Error("discovery must not run without an address"); },
    });

    await open(env);

    assert.deepEqual(options(env), ["Enter the trace service address to load workflows"]);
    assert.ok(env.element('[data-role="guided-analysis-status"]').textContent.includes("trace service address"));
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
    assert.equal(discoveryCalls(env).length, 0);
});

test("an unreachable trace service shows Waiting for trace service… instead of an error or a Docker command", async () => {
    const env = makeEnvironment({ status: () => down(env), discover: () => { throw new Error("must not run"); } });

    await open(env);

    assert.equal(stateLabel(env), "Waiting for trace service…");
    assert.equal(observed(env).dataset.state, "waiting");
    assert.deepEqual(options(env), ["Waiting for trace service…"]);
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
    const everythingShown = [stateLabel(env), env.element('[data-role="guided-observed-detail"]').textContent,
        env.element('[data-role="guided-analysis-status"]').textContent].join(" ");
    assert.ok(!/docker|compose|boutique/i.test(everythingShown));
});

test("the page retries a lightweight health check every 10 seconds and loads workflows when the service recovers", async () => {
    let up = false;
    const env = makeEnvironment({
        status: () => (up ? healthy(env) : down(env)),
        discover: () => env.json(200, report("discovered", [workflow("GET /orders")])),
    });
    await open(env);
    const before = healthCalls(env).length;

    await env.advance(9999);
    assert.equal(healthCalls(env).length, before, "no retry before 10 seconds");
    await env.advance(1);
    assert.equal(healthCalls(env).length, before + 1);
    assert.equal(discoveryCalls(env).length, 0);

    up = true;
    await env.advance(10000);

    assert.equal(discoveryCalls(env).length, 1, "discovery started without a reload or a click");
    assert.equal(stateLabel(env), "Observed workflows found");
    assert.ok(options(env)[0].startsWith("GET /orders"));
});

test("connected with zero traces is its own state, and workflows appear by themselves once traces arrive", async () => {
    let current = report("no_traces");
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(200, current) });
    await open(env);

    assert.equal(stateLabel(env), "Trace service connected · no traces yet");
    assert.equal(observed(env).dataset.state, "no_traces");
    assert.deepEqual(options(env), ["No traces recorded in the last 1h yet"]);

    current = report("discovered", [workflow("GET /orders")]);
    await env.advance(10000);
    assert.equal(stateLabel(env), "Observed workflows found");
    assert.equal(env.element('[data-role="guided-candidates"]').children.length, 1);
});

test("incomplete tracing evidence is reported as its own state, not as discovered workflows", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("incomplete_traces", [workflow("GET /orders", {
            evidenceStatus: "unusable", traceQuality: { validCount: 0, partialCount: 3, invalidCount: 0 } })])),
    });
    await open(env);

    assert.equal(observed(env).dataset.state, "incomplete_traces");
    assert.equal(stateLabel(env), "Traces recorded · tracing evidence incomplete");
    const rows = env.element('[data-role="guided-candidates"]').children;
    assert.equal(rows.length, 1);
    assert.ok(rows[0].children[2].textContent.startsWith("Trace evidence incomplete"));
});

test("discovered workflows show the root operation, entry service, trace count and participating services", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [
            workflow("POST /pay", { rootService: "api-gateway", traceCount: 7, participatingServices: ["api-gateway", "payments"] }),
        ])),
    });
    await open(env);

    const [row] = env.element('[data-role="guided-candidates"]').children;
    const [head, services, evidence] = row.children;
    assert.equal(head.children[0].textContent, "POST /pay");
    assert.equal(head.children[1].textContent, "enters at api-gateway · 7 traces");
    assert.deepEqual(services.children.map((chip) => chip.textContent), ["api-gateway", "payments"]);
    assert.ok(evidence.textContent.includes("observed only, dependency class not determined"));
    assert.ok(!/mandatory|optional|critical/i.test(row.children.map((c) => c.textContent).join(" ")));
});

test("any entry service name works: inspecting a candidate sends that candidate's own entry service", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [workflow("GET /home", { rootService: "istio-ingressgateway" })])),
    });
    await open(env);
    env.element('[data-role="guided-operation"]').value = "istio-ingressgateway GET /home";
    env.listeners.DOMContentLoaded = null;

    await env.element('[data-role="guided-inspect-button"]').listeners.click();

    const session = env.calls.find(([method, url]) => method === "POST" && url === "/api/workflows/analysis-sessions");
    assert.ok(session, "an analysis was requested");
    const body = JSON.parse(session[2]);
    assert.equal(body.entryService, "istio-ingressgateway");
    assert.equal(body.operationName, "GET /home");
});

test("the discovery request carries no entry service, and no application name is assumed", async () => {
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(200, report("no_traces")) });
    await open(env);

    const body = JSON.parse(discoveryCalls(env)[0][2]);
    assert.deepEqual(Object.keys(body).sort(), ["jaegerUrl", "lookback"]);
    assert.equal(body.lookback, "1h");
});

test("an unchanged refresh leaves the operator's selection alone and a changed one keeps it when still present", async () => {
    let current = report("discovered", [workflow("GET /a", { traceCount: 9 }), workflow("GET /b")]);
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(200, current) });
    await open(env);
    const select = env.element('[data-role="guided-operation"]');
    select.value = "frontend GET /b";

    await env.advance(30000);
    assert.equal(select.value, "frontend GET /b", "same evidence: nothing re-rendered");

    current = report("discovered", [workflow("GET /a", { traceCount: 12 }), workflow("GET /b")]);
    await env.advance(30000);
    assert.equal(select.value, "frontend GET /b", "new evidence: the choice survives the re-render");
    assert.ok(options(env)[0].includes("12 traces"));
});

test("a refresh while connected is controlled: 30 seconds, not a tight loop", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [workflow("GET /a")])),
    });
    await open(env);
    assert.equal(discoveryCalls(env).length, 1);

    await env.advance(29999);
    assert.equal(discoveryCalls(env).length, 1);
    await env.advance(1);
    assert.equal(discoveryCalls(env).length, 2);
});

test("a hidden tab makes no requests, and showing it runs discovery", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [workflow("GET /a")])),
        hidden: true,
    });
    await open(env);
    await env.advance(5 * 60 * 1000);
    assert.equal(healthCalls(env).filter(([, url]) => url.includes("jaeger_url")).length, 0, "no polling while hidden");
    assert.equal(discoveryCalls(env).length, 0);

    env.document.hidden = false;
    env.listeners.visibilitychange();
    await flush();
    assert.equal(discoveryCalls(env).length, 1);
});

test("hiding the tab pauses the retry loop and returning to it resumes it", async () => {
    const env = makeEnvironment({ status: () => down(env), discover: () => { throw new Error("must not run"); } });
    await open(env);
    const before = healthCalls(env).length;

    env.document.hidden = true;
    env.listeners.visibilitychange();
    await env.advance(60000);
    assert.equal(healthCalls(env).length, before, "paused while hidden");

    env.document.hidden = false;
    env.windowListeners.focus();
    await flush();
    assert.equal(healthCalls(env).length, before + 1, "window focus resumes the check");
});

test("repeated clicks on Refresh while a request is in flight never run two discoveries at once", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let active = 0;
    let peak = 0;
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: async () => {
            active += 1;
            peak = Math.max(peak, active);
            await gate;
            active -= 1;
            return env.json(200, report("discovered", [workflow("GET /a")]));
        },
    });
    const starting = env.listeners.DOMContentLoaded();
    await flush();
    const form = env.element('[data-role="guided-analysis-form"]');
    for (let i = 0; i < 5; i += 1) form.listeners.submit({ preventDefault() {} });
    await flush();
    assert.equal(discoveryCalls(env).length, 1);

    release();
    await starting;
    await flush();
    await flush();
    assert.equal(peak, 1, "never two requests at the same time");
    assert.equal(discoveryCalls(env).length, 2, "the five clicks became a single follow-up");
});

test("changing the time window asks again with the new window", async () => {
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(200, report("no_traces")) });
    await open(env);

    env.element("#guided-lookback").value = "6h";
    env.element("#guided-lookback").listeners.change();
    await flush();

    assert.equal(JSON.parse(discoveryCalls(env).at(-1)[2]).lookback, "6h");
});

test("a rejected connection setting stops the retries and says why", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(422, { detail: "Jaeger host is not approved; configure WORKFLOW_JAEGER_ALLOWED_HOSTS" }),
    });
    await open(env);
    assert.equal(observed(env).dataset.state, "error");
    assert.ok(env.element('[data-role="guided-observed-detail"]').textContent.includes("not approved"));

    await env.advance(5 * 60 * 1000);
    assert.equal(discoveryCalls(env).length, 1, "a setting that will not fix itself is not retried");
});

test("a discovery failure after a healthy check ends the loading state with a clear message", async () => {
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(503, { detail: "Jaeger server unreachable" }) });
    await open(env);

    assert.deepEqual(options(env), ["No workflows available — check advanced settings"]);
    assert.notEqual(env.element('[data-role="guided-inspect-button"]').textContent, "Loading…");
});

test("a trace-service address the server refuses is explained, with the server's reason, and not retried", async () => {
    const reason = "Jaeger host 'jaeger.local' is not approved; configure WORKFLOW_JAEGER_ALLOWED_HOSTS";
    let first = true;
    const env = makeEnvironment({
        // The server's own setting is trusted, so the prefill succeeds; the address the page then checks is refused.
        status: () => {
            if (first) { first = false; return healthy(env); }
            return env.json(422, { detail: { code: "JAEGER_ADDRESS_NOT_APPROVED", message: reason, retryable: false } });
        },
        discover: () => { throw new Error("discovery must not run for a refused address"); },
    });
    await open(env);

    assert.equal(observed(env).dataset.state, "error");
    assert.equal(env.element('[data-role="guided-observed-detail"]').textContent, reason);
    const checks = healthCalls(env).length;
    await env.advance(5 * 60 * 1000);
    assert.equal(healthCalls(env).length, checks, "a refused address is not retried");
    assert.equal(discoveryCalls(env).length, 0);
});

test("services the discovery limit kept from being read are named, never hidden", async () => {
    const env = makeEnvironment({
        status: () => healthy(env),
        discover: () => env.json(200, report("discovered", [workflow("GET /a")], {
            entryDetection: "call_graph", servicesScanned: ["frontend"], servicesCalledByOthers: ["backend"],
            servicesSkipped: ["batch-reporter"], servicesUnexamined: ["batch-reporter"], truncated: true,
        })),
    });
    await open(env);

    const detail = env.element('[data-role="guided-observed-detail"]').textContent;
    assert.ok(detail.includes("batch-reporter"), detail);
    assert.ok(detail.includes("WORKFLOW_DISCOVERY_MAX_SERVICES"), detail);
    assert.ok(detail.includes("1 entry service"), detail);
});

test("without a call graph the page says every recorded service was read", async () => {
    const env = makeEnvironment({ status: () => healthy(env), discover: () => env.json(200, report("discovered", [workflow("GET /a")])) });
    await open(env);

    assert.ok(env.element('[data-role="guided-observed-detail"]').textContent.includes("2 of 2 recorded services read"));
});
