// Integration test for the recommendations page: the real page script, API helper and display components run
// against a stubbed network and a fake DOM. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const SCRIPTS = ["api.js", "connection-state.js", "recommendation-render.js", "components/recommendations.js"].map((p) => [p, read(p)]);

function makeEnvironment(routes) {
    const registry = new Map();
    const element = (key) => {
        if (registry.has(key)) return registry.get(key);
        const node = {
            key, children: [], attributes: {}, className: "", textContent: "", style: {}, listeners: {}, disabled: false, value: "",
            classList: { _set: new Set(), add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); }, toggle(c) { const on = !this._set.has(c); on ? this._set.add(c) : this._set.delete(c); return on; } },
            setAttribute(name, value) { this.attributes[name] = value; },
            removeAttribute(name) { delete this.attributes[name]; },
            append(...nodes) { this.children.push(...nodes); },
            appendChild(node) { this.children.push(node); return node; },
            replaceChildren(...nodes) { this.children = [...nodes]; },
            addEventListener(type, fn) { this.listeners[type] = fn; },
            querySelector: (selector) => element(selector),
        };
        registry.set(key, node);
        return node;
    };
    const template = element('[data-role="page-template"]');
    template.content = { cloneNode: () => ({ fragment: true }) };

    const listeners = {};
    const document = {
        querySelector: (selector) => element(selector),
        getElementById: (id) => element(`#${id}`),
        createElement: (tag) => ({
            tag, children: [], attributes: {}, className: "", textContent: "", style: {}, listeners: {},
            setAttribute(name, value) { this.attributes[name] = value; },
            append(...nodes) { this.children.push(...nodes); },
            appendChild(node) { this.children.push(node); return node; },
            replaceChildren(...nodes) { this.children = [...nodes]; },
            addEventListener(type, fn) { this.listeners[type] = fn; },
        }),
        createTextNode: (text) => ({ textContent: text }),
        addEventListener: (type, fn) => { listeners[type] = fn; },
    };
    const calls = [];
    const fetch = async (url, init = {}) => {
        calls.push([init.method || "GET", url]);
        const route = routes[`${init.method || "GET"} ${url}`];
        if (!route) throw new Error(`unexpected request ${init.method || "GET"} ${url}`);
        return {
            ok: route.status < 400, status: route.status,
            headers: { get: (name) => (route.headers || {})[name] ?? null },
            json: async () => route.body,
        };
    };

    const window = { document };
    const ui = {
        formatCodeLabel: (code) => String(code).split("_").map((w) => w.charAt(0) + w.slice(1).toLowerCase()).join(" "),
        initJsonPanel: (panel, data) => { if (panel) panel.jsonShown = data; },
        updateLastRefreshed: (el) => { if (el) el.textContent = "refreshed"; },
    };
    window.ResilienceUi = ui;
    const sandbox = { window, document, fetch, console, AbortController, setTimeout, clearTimeout, URLSearchParams };
    vm.createContext(sandbox);
    for (const [name, code] of SCRIPTS) vm.runInContext(code, sandbox, { filename: name });
    return { element, listeners, calls, region: element('[data-role="page-region"]') };
}

function text(node) {
    const parts = [];
    const visit = (n) => { if (n.textContent) parts.push(n.textContent); (n.children || []).forEach(visit); };
    visit(node);
    return parts.join(" | ");
}

const candidate = (overrides = {}) => ({
    id: "rec-live", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true,
    predictedBlastRadius: 30, riskReduction: 40, resourceCost: "Low", confidence: 0.85, score: 0.9, ...overrides,
});
const liveReport = { workflowId: "wf", recommendations: [candidate()], selectedRecommendationId: "rec-live" };

async function openPage(routes) {
    const env = makeEnvironment(routes);
    await env.listeners.DOMContentLoaded();
    return env;
}

test("with no live recommendation the page shows the explicit reason and no recommendation panels", async () => {
    const env = await openPage({
        "GET /api/recommendations/live": {
            status: 409,
            body: { detail: { code: "RECOMMENDATION_EVIDENCE_UNAVAILABLE", message: "No recommendation has been generated from live cluster evidence.", component: "recommendation", retryable: false } },
        },
    });

    assert.ok(text(env.region).includes("No recommendation has been generated from live cluster evidence."));
    assert.ok(text(env.region).includes("RECOMMENDATION_EVIDENCE_UNAVAILABLE"));
    assert.equal(env.element('[data-role="rec-candidate-list"]').children.length, 0);
    assert.equal(env.element('[data-role="source-badge"]').textContent, "No live recommendation");
    assert.deepEqual(env.calls.map((c) => c[1]), ["/api/recommendations/live"]); // nothing else was requested
});

test("with a live report and no other evidence every missing piece is stated, never filled in", async () => {
    const env = await openPage({
        "GET /api/recommendations/live": { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: { detail: "Approval not found" } },
    });

    assert.ok(text(env.element('[data-role="rec-candidate-list"]')).includes("Topology Spread"));
    assert.ok(text(env.element('[data-role="rec-candidate-list"]')).includes("Baseline not provided"));
    assert.ok(text(env.element('[data-role="rec-validation"]')).includes("No post-deployment validation evidence"));
    assert.ok(text(env.element('[data-role="rec-yaml"]')).includes("No Kubernetes YAML proposal has been generated"));
    assert.ok(text(env.element('[data-role="rec-placement-current"]')).includes("No topology evidence"));
    assert.ok(text(env.element('[data-role="rec-placement-predicted"]')).includes("No predicted placement"));
    assert.equal(env.element('[data-metric="rec-baseline-blast-radius"]').textContent, "—");
    assert.equal(env.element('[data-metric="rec-feasible-count"]').textContent, "1");
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Live evidence");
});

test("the ranking table lists the candidate and the approval gate offers a request", async () => {
    const env = await openPage({
        "GET /api/recommendations/live": { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
    });

    assert.equal(env.element('[data-role="rec-candidate-table-body"]').children.length, 1);
    const actions = env.element('[data-role="approval-actions"]');
    assert.ok(actions.children.some((child) => child.id === "btn-request-approval"));
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("No Approval Requested"));
});

test("a stored YAML proposal is shown, and an unverified approval cannot be approved from the page", async () => {
    const yaml = "apiVersion: apps/v1\nkind: Deployment";
    const env = await openPage({
        "GET /api/recommendations/live": { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": {
            status: 200, headers: { "X-Evidence-Provenance": "unverified" },
            body: { status: "PENDING_APPROVAL", proposalVersion: "v1", yamlSnapshot: yaml, decisionNote: null },
        },
    });

    const pre = env.element('[data-role="rec-yaml"]').children[0].children.find((child) => child.tag === "pre");
    assert.equal(pre.textContent, yaml);
    const actions = env.element('[data-role="approval-actions"]').children;
    const approve = actions.find((child) => child.id === "btn-show-approve");
    assert.equal(approve.disabled, true);
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("no verifiable link to live cluster evidence"));
});

test("a failing approval lookup is reported on the page and does not hide the recommendation", async () => {
    const env = await openPage({
        "GET /api/recommendations/live": { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": { status: 500, body: { detail: "boom" } },
    });

    assert.ok(text(env.element('[data-role="rec-candidate-list"]')).includes("Topology Spread"));
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Could not load the approval state."));
});

async function openWithInputs(routes, values) {
    const env = makeEnvironment(routes);
    for (const [role, value] of Object.entries(values)) env.element(`[data-role="${role}"]`).value = value;
    await env.listeners.DOMContentLoaded();
    return env;
}

test("the filled-in workflow, failed node and namespace are sent with the request", async () => {
    const url = "/api/recommendations/live?workflow_id=checkout&failed_node=w1&namespace=shop";
    const env = await openWithInputs({
        [`GET ${url}`]: { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
    }, { "workflow-id": " checkout ", "failed-node": "w1", namespace: "shop" });

    assert.equal(env.calls[0][1], url);
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Live evidence");
});

test("a blank input is not sent, so the server says what is missing", async () => {
    const env = await openWithInputs({
        "GET /api/recommendations/live?workflow_id=checkout": { status: 200, body: { ...liveReport, recommendations: [], selectedRecommendationId: null } },
    }, { "workflow-id": "checkout", "failed-node": "   ", namespace: "" });

    assert.equal(env.calls[0][1], "/api/recommendations/live?workflow_id=checkout");
});

test("requesting an approval sends the same inputs, because the server recomputes the recommendation", async () => {
    const query = "?workflow_id=checkout&namespace=shop";
    const env = await openWithInputs({
        [`GET /api/recommendations/live${query}`]: { status: 200, body: liveReport },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
        [`POST /api/recommendations/rec-live/request-approval${query}`]: {
            status: 200, headers: { "X-Evidence-Provenance": "verified" },
            body: { status: "PENDING_APPROVAL", proposalVersion: "v1.0", yamlSnapshot: null, decisionNote: null },
        },
    }, { "workflow-id": "checkout", namespace: "shop" });

    const request = env.element('[data-role="approval-actions"]').children.find((child) => child.id === "btn-request-approval");
    await request.listeners.click();

    assert.ok(env.calls.some(([method, url]) => method === "POST" && url === `/api/recommendations/rec-live/request-approval${query}`));
}
);

