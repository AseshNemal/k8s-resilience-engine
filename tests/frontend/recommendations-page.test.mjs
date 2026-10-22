// Integration test for the recommendations page: the real page script, context controller, API helper and display components run
// against a stubbed network and a fake DOM. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const SCRIPTS = ["api.js", "connection-state.js", "recommendation-render.js", "recommendation-context.js", "components/recommendations.js"].map((p) => [p, read(p)]);

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

    const windowListeners = {};
    const window = { document, addEventListener: (type, fn) => { windowListeners[type] = fn; } };
    const ui = {
        formatCodeLabel: (code) => String(code).split("_").map((w) => w.charAt(0) + w.slice(1).toLowerCase()).join(" "),
        initJsonPanel: (panel, data) => { if (panel) panel.jsonShown = data; },
        updateLastRefreshed: (el) => { if (el) el.textContent = "refreshed"; },
    };
    window.ResilienceUi = ui;
    const sandbox = { window, document, fetch, console, AbortController, setTimeout, clearTimeout, URLSearchParams };
    vm.createContext(sandbox);
    for (const [name, code] of SCRIPTS) vm.runInContext(code, sandbox, { filename: name });
    return { element, listeners, windowListeners, calls, region: element('[data-role="page-region"]') };
}

function text(node) {
    const parts = [];
    const visit = (n) => { if (n.textContent) parts.push(n.textContent); (n.children || []).forEach(visit); };
    visit(node);
    return parts.join(" | ");
}

const NOT_READY = (reason = "Not feasible: a single replica cannot survive the loss of its node.") => ({ ready: false, reason, requiresJustification: false });
const cand = (over = {}) => ({
    id: "rec-live", type: "TOPOLOGY_SPREAD", service: "payment", rootCause: "All replicas run on one node.",
    recommendedChange: "Add a topologySpreadConstraint on kubernetes.io/hostname", feasible: true,
    feasibility: { status: "FEASIBLE", explanation: "a placement was found", findings: [], missingEvidence: [] },
    currentPlacement: { payment: [{ pod: "payment-1", node: "w1", ready: true }] }, declaredReplicas: 3, readyReplicas: 3, podsToRecreate: [],
    predicted: { blastRadiusBefore: 100, blastRadiusAfter: 30, riskReduction: 70 }, cost: { resource: "LOW", note: "Adds no replicas.", configuration: "1 Deployment pod-template edit(s)" },
    confidence: 0.85, score: 0.9, ranking: null, selected: true, selectionJustification: "Selected: top-ranked feasible candidate.",
    approval: { ready: true, reason: null, requiresJustification: false }, simulatedDeployment: null, notes: [], ...over,
});
const reportOf = (candidates, selected) => ({
    workflowId: "wf", selectedRecommendationId: selected,
    recommendations: candidates.map((c) => ({ id: c.id, type: c.type, service: c.service, feasible: c.feasible, predictedBlastRadius: c.predicted.blastRadiusAfter,
        riskReduction: c.predicted.riskReduction, resourceCost: c.cost.resource, confidence: c.confidence, score: c.score })),
});
const detailOf = (candidates, selected, outcome) => ({
    outcome: outcome || { state: candidates.some((c) => c.approval.ready) ? "READY" : "CANDIDATES_NOT_READY", message: candidates.some((c) => c.approval.ready) ? "1 candidate(s) can be put to a developer for approval." : "1 candidate(s) were evaluated and none can be approved; the reason for each is shown." },
    report: reportOf(candidates, selected),
    evidence: { workflowId: "wf", namespace: "shop", blastRadiusBefore: 100, failedNode: "w1", failedNodeChosen: "highest blast radius", isPrediction: true },
    candidates, currentDeployment: { nodes: [{ name: "w1" }, { name: "w2" }], services: [] }, yaml: { available: false }, validation: { available: false }, applied: false,
});
const ev = (id, state, extra = {}) => ({ id, label: { cluster: "Connected cluster", application: "Application", topology: "Deployment topology", workflow: "Approved frozen workflow", failureImpact: "Failure impact" }[id], state, detail: `${id} ${state}`, nextAction: null, ...extra });
const contextOf = (over = {}) => ({
    cluster: { state: "available", fingerprint: "cluster-A" }, namespaces: [{ namespace: "shop", services: ["payment", "web"] }], namespace: "shop", namespaceChoice: "only-application",
    workflows: [{ workflowId: "checkout", freezeId: "f1", frozenAt: "2026-10-20T10:00:00Z", services: ["payment", "web"], applicable: true, reason: null }],
    workflowId: "checkout", workflowChoice: "only-applicable",
    evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "available"), ev("failureImpact", "pending")],
    nextActions: [], ready: true, readOnly: true, ...over,
});
const DETAIL = "GET /api/recommendations/live/detail?workflow_id=checkout&namespace=shop";
const BASE = { "GET /_extension/cluster": { status: 200, body: { context: "docker-desktop" } }, "GET /api/recommendations/context": { status: 200, body: contextOf() } };

async function openPage(routes) {
    const env = makeEnvironment({ ...BASE, ...routes });
    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));   // let the approval lookup that follows a render finish
    return env;
}

test("the connected cluster, the only application and the only workflow are used without typing anything", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    assert.deepEqual(env.calls.map((c) => `${c[0]} ${c[1]}`), ["GET /_extension/cluster", "GET /api/recommendations/context", DETAIL.slice(4).replace(/^/, "GET "), "GET /api/recommendations/rec-live/approval"]);
    assert.equal(env.element('[data-role="rec-cluster"]').textContent, "Connected cluster: docker-desktop");
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Live evidence");
    assert.equal(env.element('[data-role="rec-namespace-select"]').value, "shop");
    assert.equal(env.element('[data-role="rec-namespace-select"]').disabled, true, "one application: nothing to choose");
    assert.equal(env.element('[data-role="rec-workflow-select"]').value, "checkout");
    assert.equal(env.element('[data-role="rec-evidence"]').children.length, 5);
    assert.ok(text(env.element('[data-role="rec-evidence"]')).includes("Predicted blast radius 100% if w1 fails"));
});

test("Bookinfo-style evidence with no feasible change: no approval, the reasons are shown, and it is not called healthy", async () => {
    const infeasible = cand({ id: "rec-ratings", service: "ratings", feasible: false, selected: false, score: 0, declaredReplicas: 1,
        feasibility: { status: "INFEASIBLE", explanation: "a single replica", findings: [], missingEvidence: [] },
        predicted: { blastRadiusBefore: 100, blastRadiusAfter: 100, riskReduction: 0 }, approval: NOT_READY(),
        notes: ["This service runs a single replica. A spread constraint cannot make one replica survive the loss of the node it runs on."] });
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([infeasible], null) } });

    assert.ok(text(env.element('[data-role="rec-outcome"]')).includes("none can be approved"));
    const details = text(env.element('[data-role="rec-detail-list"]'));
    for (const piece of ["ratings", "All replicas run on one node.", "single replica", "100% now → 100% predicted", "Not ready for approval: Not feasible"]) assert.ok(details.includes(piece), piece);
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Nothing to approve"));
    assert.equal(env.element('[data-role="approval-actions"]').children.length, 0, "no request button for a candidate that cannot be approved");
    assert.ok(text(env.element('[data-role="rec-yaml"]')).includes("No Kubernetes YAML proposal has been generated"));
    assert.ok(!calls_include(env, "request-approval"));
});

function calls_include(env, fragment) { return env.calls.some(([, url]) => url.includes(fragment)); }

test("no candidate at all says it is not a finding that the system is resilient", async () => {
    const message = "The evidence is complete, but the engine generated no candidate change. This is not a finding that the application is resilient.";
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([], null, { state: "NO_CANDIDATES", message }) } });

    assert.ok(text(env.element('[data-role="rec-outcome"]')).includes("not a finding that the application is resilient"));
    assert.ok(text(env.element('[data-role="rec-detail-list"]')).includes("No candidate was generated"));
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Nothing to approve"));
});

test("a missing frozen workflow shows the missing evidence and the exact next action, and requests no recommendation", async () => {
    const missing = contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"),
            ev("workflow", "missing", { detail: "No workflow has been approved and frozen yet.", nextAction: "Open Workflow dependencies ... freeze its graph" }), ev("failureImpact", "missing")],
        nextActions: ["Open Workflow dependencies ... freeze its graph"] });
    const env = await openPage({ "GET /api/recommendations/context": { status: 200, body: missing } });

    assert.ok(text(env.region).includes("No workflow has been approved and frozen yet."));
    assert.ok(text(env.region).includes("Next: Open Workflow dependencies ... freeze its graph"));
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Evidence missing");
    assert.equal(env.element('[data-role="rec-candidate-list"]').children.length, 0);
    assert.ok(!calls_include(env, "/live/detail"));
    assert.equal(env.element('[data-role="rec-workflow-select"]').disabled, true);
    assert.ok(text(env.element('[data-role="rec-evidence"]')).includes("✖ Approved frozen workflow"));
});

test("incomplete failure-impact evidence names the services and what to do", async () => {
    const env = await openPage({ [DETAIL]: { status: 409, body: { detail: { code: "TOPOLOGY_INCOMPLETE", message: "The topology is not complete enough for the services this workflow depends on.",
        component: "failure_impact", retryable: false, details: { services: [{ service: "ratings", reasons: ["NO_PLACEMENT_OBSERVED"] }] } } } } });

    const shown = text(env.region);
    assert.ok(shown.includes("ratings: NO_PLACEMENT_OBSERVED") && shown.includes("TOPOLOGY_INCOMPLETE") && shown.includes("Deployment topology"));
    assert.ok(text(env.element('[data-role="rec-evidence"]')).includes("! Failure impact"));
    assert.equal(env.element('[data-role="rec-candidate-list"]').children.length, 0);
});

test("with several workflows a dropdown choice, not a typed ID, starts the recommendation", async () => {
    const two = contextOf({ workflows: [
        { workflowId: "a", freezeId: "f", frozenAt: null, services: ["payment"], applicable: true, reason: null },
        { workflowId: "checkout", freezeId: "f", frozenAt: null, services: ["payment"], applicable: true, reason: null }], workflowId: null, workflowChoice: "choose", ready: false });
    const chosen = contextOf({ workflows: two.workflows, workflowId: "checkout", workflowChoice: "requested" });
    const env = await openPage({
        "GET /api/recommendations/context": { status: 200, body: two },
        "GET /api/recommendations/context?namespace=shop": { status: 200, body: two },
        "GET /api/recommendations/context?namespace=shop&workflow_id=checkout": { status: 200, body: chosen },
        [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
    });
    assert.ok(text(env.region).includes("none was chosen for you"));
    assert.ok(!calls_include(env, "/live/detail"));

    const select = env.element('[data-role="rec-workflow-select"]');
    select.value = "checkout";
    await select.listeners.change();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.ok(calls_include(env, "/live/detail?workflow_id=checkout&namespace=shop"));
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Live evidence");
});

test("when the connected cluster changes the page starts over and sends nothing chosen before", async () => {
    const other = contextOf({ cluster: { state: "available", fingerprint: "cluster-B" }, namespaces: [{ namespace: "bookinfo", services: ["ratings"] }], namespace: "bookinfo",
        workflows: [{ workflowId: "bookinfo-wf", freezeId: "f", frozenAt: null, services: ["ratings"], applicable: true, reason: null }], workflowId: "bookinfo-wf" });
    let identity = "docker-desktop";
    const routes = { ...BASE, "GET /_extension/cluster": () => ({ status: 200, body: { context: identity } }),
        "GET /api/recommendations/context": () => ({ status: 200, body: identity === "docker-desktop" ? contextOf() : other }),
        [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
        "GET /api/recommendations/live/detail?workflow_id=bookinfo-wf&namespace=bookinfo": { status: 200, body: detailOf([], null, { state: "NO_CANDIDATES", message: "none" }) } };
    const env = makeEnvironment(Object.fromEntries(Object.entries(routes).map(([k, v]) => [k, typeof v === "function" ? { get status() { return v().status; }, get body() { return v().body; } } : v])));
    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const before = env.calls.length;

    identity = "kind-other";
    await env.windowListeners["resilience-cluster-changed"]();
    await new Promise((resolve) => setTimeout(resolve, 0));

    const after = env.calls.slice(before).map(([, url]) => url);
    assert.ok(!after.some((url) => url.includes("checkout") || url.includes("namespace=shop")), `old choices were sent: ${after}`);
    assert.equal(env.element('[data-role="rec-namespace-select"]').value, "bookinfo");
    assert.equal(env.element('[data-role="rec-cluster"]').textContent, "Connected cluster: kind-other");
});

// ---- the approval gate keeps its behaviour, now keyed to the server's own verdict ----------------------------------

test("a candidate the server says is ready can be requested, with the same selections sent so the server recomputes it", async () => {
    const query = "?workflow_id=checkout&namespace=shop";
    const env = await openPage({
        [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
        [`POST /api/recommendations/rec-live/request-approval${query}`]: { status: 200, headers: { "X-Evidence-Provenance": "verified" }, body: { status: "PENDING_APPROVAL", proposalVersion: "v1.0", yamlSnapshot: null, decisionNote: null } },
    });

    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("No Approval Requested"));
    const request = env.element('[data-role="approval-actions"]').children.find((child) => child.id === "btn-request-approval");
    assert.ok(!request.disabled);
    await request.listeners.click();
    assert.ok(env.calls.some(([method, url]) => method === "POST" && url === `/api/recommendations/rec-live/request-approval${query}`));
});

test("a feasible-looking candidate the server does not allow cannot be requested, and the reason is shown", async () => {
    const blocked = cand({ approval: NOT_READY("Not ranked: no predicted improvement.") });
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([blocked], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    const request = env.element('[data-role="approval-actions"]').children.find((child) => child.id === "btn-request-approval");
    assert.equal(request.disabled, true);
    assert.equal(request.title, "Not ranked: no predicted improvement.");
});

test("a stored YAML proposal is shown, and an unverified approval cannot be approved from the page", async () => {
    const yaml = "apiVersion: apps/v1\nkind: Deployment";
    const env = await openPage({
        [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") },
        "GET /api/recommendations/rec-live/approval": { status: 200, headers: { "X-Evidence-Provenance": "unverified" }, body: { status: "PENDING_APPROVAL", proposalVersion: "v1", yamlSnapshot: yaml, decisionNote: null } },
    });

    const pre = env.element('[data-role="rec-yaml"]').children[0].children.find((child) => child.tag === "pre");
    assert.equal(pre.textContent, yaml);
    const approve = env.element('[data-role="approval-actions"]').children.find((child) => child.id === "btn-show-approve");
    assert.equal(approve.disabled, true);
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("no verifiable link to live cluster evidence"));
});

test("a failing approval lookup is reported on the page and does not hide the recommendation", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 500, body: { detail: "boom" } } });

    assert.ok(text(env.element('[data-role="rec-candidate-list"]')).includes("Topology Spread"));
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Could not load the approval state."));
});

test("the page only reads: it never sends a change except an approval request the person clicked", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });
    assert.ok(env.calls.every(([method]) => method === "GET"));
});
