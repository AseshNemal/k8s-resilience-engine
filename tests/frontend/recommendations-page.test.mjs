// Integration test for the recommendations page: the real page script, context controller, API helper and display components run
// against a stubbed network and a fake DOM. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const SCRIPTS = ["api.js", "connection-state.js", "recommendation-render.js", "recommendation-context.js", "recommendation-operation.js", "components/recommendations.js"].map((p) => [p, read(p)]);

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
            prepend(...nodes) { this.children.unshift(...nodes); },
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
    // The applied-change panel's skeleton. (Its own lifecycle is tested against a tree-faithful DOM in recommendation-operation.test.mjs.)
    element('[data-role="operation-template"]').content = { cloneNode: () => ({ fragment: true }) };
    routes = { "GET /api/recommendations/applications/current": { status: 200, body: { application: null, tier: null } }, ...routes };

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
    const bodies = [];   // the JSON body of each request, in the same order as `calls`
    const fetch = async (url, init = {}) => {
        calls.push([init.method || "GET", url]);
        bodies.push(init.body ? JSON.parse(init.body) : null);
        const entry = routes[`${init.method || "GET"} ${url}`];
        if (!entry) throw new Error(`unexpected request ${init.method || "GET"} ${url}`);
        const route = typeof entry === "function" ? entry() : entry;      // a function answers from the server's state at that moment
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
    // Timers the page starts (the panel's polling) must not keep the test process alive after the test has finished.
    const unrefTimeout = (fn, ms) => { const id = setTimeout(fn, ms); if (id && id.unref) id.unref(); return id; };
    const sandbox = { window, document, fetch, console, AbortController, setTimeout: unrefTimeout, clearTimeout, URLSearchParams };
    vm.createContext(sandbox);
    for (const [name, code] of SCRIPTS) vm.runInContext(code, sandbox, { filename: name });
    return { element, listeners, windowListeners, calls, bodies, region: element('[data-role="page-region"]') };
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

    assert.deepEqual(env.calls.map((c) => `${c[0]} ${c[1]}`), ["GET /_extension/cluster", "GET /api/recommendations/applications/current", "GET /api/recommendations/context", DETAIL.slice(4).replace(/^/, "GET "), "GET /api/recommendations/rec-live/approval"]);
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

// ---- declared workflows: the research tier's own door, never the production controls ---------------------------------------------------------

const declaredWf = { workflowId: "checkout", declaredBy: "asesh", services: ["payment", "web"], applicable: true, reason: null, declared: true, approvable: false };
const declaredContext = () => contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false, declaredWorkflows: [declaredWf],
    evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "missing"), ev("failureImpact", "missing")] });
const D_DETAIL = "GET /api/recommendations/declared/detail?workflow_id=checkout&namespace=shop";
const researchOffer = (over = {}) => ({ available: true, scope: "Research validation on a designated disposable test cluster only.", reason: null, ...over });
const declaredCandidate = (over = {}) => cand({ approval: { ready: false, reason: "declared", requiresJustification: false, declared: true }, researchApproval: researchOffer(), ...over });
const declaredBody = (candidate) => ({ ...detailOf([candidate], "rec-live", { state: "DECLARED_NOT_APPROVABLE", message: "Evaluated from a declared workflow." }),
    declared: { workflowId: "checkout", observed: false, approvable: false, notApprovableBecause: "declared, see the research tier" } });

async function openDeclared(extraRoutes = {}, candidate = declaredCandidate()) {
    const env = await openPage({
        "GET /api/recommendations/context": { status: 200, body: declaredContext() },
        "GET /api/recommendations/context?namespace=shop": { status: 200, body: declaredContext() },
        [D_DETAIL]: { status: 200, body: declaredBody(candidate) },
        "GET /api/recommendations/declared/rec-live/approval": { status: 404, body: {} }, ...extraRoutes });
    const select = env.element('[data-role="rec-workflow-select"]');
    select.value = "declared:checkout";
    await select.listeners.change();
    await new Promise((resolve) => setTimeout(resolve, 0));
    return env;
}

function allNodes(node, found = []) { found.push(node); (node.children || []).forEach((child) => allNodes(child, found)); return found; }
const byId = (env, id) => allNodes(env.element('[data-role="approval-state-container"]')).concat(allNodes(env.element('[data-role="approval-actions"]'))).find((n) => n.id === id);

test("a declared workflow is asked for through the declared route, and the badge and the notice say it is declared", async () => {
    const env = await openDeclared();

    assert.ok(env.calls.some(([m, u]) => m === "GET" && u.includes("/declared/detail?workflow_id=checkout&namespace=shop")));
    assert.ok(!calls_include(env, "/live/detail"));
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Declared workflow · not approvable");
    assert.ok(text(env.region).includes("declared, see the research tier"), "the standing notice");
});

test("for a declared workflow the production request button is replaced by the research form, and the production route is never used", async () => {
    const env = await openDeclared();

    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("No research approval requested") && shown.includes("Research validation on a designated disposable test cluster only."));
    assert.ok(byId(env, "research-ack-by") && byId(env, "research-ack-text") && byId(env, "btn-request-research"));
    assert.equal(byId(env, "btn-request-approval"), undefined, "no production request button");
    assert.ok(env.calls.some(([, u]) => u === "/api/recommendations/declared/rec-live/approval"), "the approval state is read from the research route");
    assert.ok(!calls_include(env, "/api/recommendations/rec-live/approval"));
});

test("the research request sends the named person and the written acknowledgement to the research route, and shows the pending state", async () => {
    const pending = { id: 1, recommendationId: "rec-live", proposalVersion: "v1.0", status: "PENDING_APPROVAL", requestedAt: "2026-10-23T00:00:00Z", requestedBy: "asesh",
        recommendationSnapshot: {}, yamlSnapshot: null, evaluationSnapshot: {} };
    const env = await openDeclared({ "POST /api/recommendations/declared/rec-live/request-approval?workflow_id=checkout&namespace=shop":
        { status: 200, body: pending, headers: { "X-Evidence-Provenance": "research-declared" } } });
    byId(env, "research-ack-by").value = "asesh";
    byId(env, "research-ack-text").value = "I understand this workflow is declared by me, not observed, and approve it for research validation only.";

    await byId(env, "btn-request-research").listeners.click();

    const index = env.calls.findIndex(([m, u]) => m === "POST" && u.includes("/declared/rec-live/request-approval"));
    assert.ok(index >= 0);
    assert.deepEqual(env.bodies[index], { recommendationId: "rec-live", proposalVersion: "v1.0", acknowledgedBy: "asesh",
        acknowledgement: "I understand this workflow is declared by me, not observed, and approve it for research validation only." });
    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("PENDING_APPROVAL") && shown.includes("Research tier"), shown);
    assert.ok(byId(env, "btn-show-approve") && byId(env, "btn-show-reject"));
    assert.ok(!byId(env, "btn-show-approve").disabled, "a research-declared record can be approved");
});

test("a research request without a name is stopped on the page and nothing is sent", async () => {
    const env = await openDeclared();
    byId(env, "research-ack-by").value = "   ";

    await byId(env, "btn-request-research").listeners.click();

    assert.ok(!env.calls.some(([m]) => m === "POST"));
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Enter your name"));
});

test("a declared candidate the server does not offer to the research tier gets a disabled button and the reason", async () => {
    const env = await openDeclared({}, declaredCandidate({ researchApproval: researchOffer({ available: false, reason: "not verified feasible and ranked" }) }));

    assert.equal(byId(env, "btn-request-research").disabled, true);
    assert.equal(byId(env, "research-ack-by"), undefined, "no form when there is nothing to request");
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("not verified feasible and ranked"));
});

test("a frozen workflow still uses the production request button and routes, unchanged", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    assert.ok(byId(env, "btn-request-approval"));
    assert.equal(byId(env, "btn-request-research"), undefined);
    assert.ok(!calls_include(env, "/declared"));
});

// ---- Apply and Reset: a separate, confirmed step after an approval, on the connected cluster -----------------------------------------------

const approvedRecord = { id: 1, recommendationId: "rec-live", proposalVersion: "v1.0", status: "APPROVED", requestedAt: "2026-10-23T00:00:00Z", requestedBy: "asesh",
    decidedBy: "asesh", recommendationSnapshot: {}, yamlSnapshot: null, evaluationSnapshot: {} };
const applicationOf = (status, over = {}) => ({ applicationId: "apply-1", recommendationId: "rec-live", status, deployments: ["db"], namespace: "shop", appliedBy: "asesh",
    resetBy: null, detail: null, ...over });
const applicationStatus = (over = {}) => ({ recommendationId: "rec-live", application: null, rolloutComplete: null, canApply: true, canReset: false, reason: null, tier: "research", ...over });
const D_APPROVAL = "GET /api/recommendations/declared/rec-live/approval";
const D_APPLICATION = "GET /api/recommendations/declared/rec-live/application";
const openApproved = (application, extra = {}) => openDeclared({
    [D_APPROVAL]: { status: 200, headers: { "X-Evidence-Provenance": "research-declared" }, body: approvedRecord },
    [D_APPLICATION]: typeof application === "function" ? application : { status: 200, body: application }, ...extra });
const posts = (env) => env.calls.map(([m, u], i) => [m, u, env.bodies[i]]).filter(([m]) => m === "POST");
const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

test("an approved recommendation offers Apply, read from the research route, and nothing is applied by showing it", async () => {
    const env = await openApproved(applicationStatus());

    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("Apply to the connected cluster"), shown);
    assert.ok(byId(env, "btn-apply") && byId(env, "apply-by"));
    assert.equal(byId(env, "btn-reset"), undefined, "Reset belongs to the applied-change panel, which outlives this box");
    assert.deepEqual(posts(env), [], "reading changes nothing");
});

test("Apply asks once more before it sends, needs a name, shows Applying change… before the answer, and hands over to the panel", async () => {
    let release;
    const answer = new Promise((resolve) => { release = resolve; });
    let server = applicationStatus();                                          // what the server will say once it has recorded the apply
    const env = await openApproved(() => ({ status: 200, body: server }), { "POST /api/recommendations/declared/rec-live/apply": { status: 200, body: answer } });

    await byId(env, "btn-apply").listeners.click();
    assert.deepEqual(posts(env), [], "the first click only asks");
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("patches only the Deployments in the approved YAML"));

    byId(env, "apply-by").value = "   ";
    await byId(env, "btn-confirm-apply").listeners.click();
    assert.deepEqual(posts(env), [], "a name is required");
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("Enter your name"));

    byId(env, "apply-by").value = "asesh";
    const sent = byId(env, "btn-confirm-apply").listeners.click();
    await settle();

    assert.deepEqual(posts(env), [["POST", "/api/recommendations/declared/rec-live/apply", { appliedBy: "asesh" }]]);
    assert.equal(env.element('[data-role="op-headline"]').textContent, "Applying change…", "on screen while the request is still out");
    assert.equal(env.element('[data-role="rec-operation"]').hidden, false);
    assert.equal(byId(env, "btn-apply").disabled, true, "the box cannot send a second apply");
    server = applicationStatus({ application: applicationOf("APPLIED"), rolloutComplete: false, canApply: false, canReset: true,
        operation: { direction: "apply", stage: "rollout", tone: "busy", headline: "Rolling out…", nextAction: "wait", steps: [], inProgress: true, pollMs: 3000, connectionLost: false },
        analysis: {}, states: null });
    release(server);
    await sent;
    await settle();

    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("db in shop: applied") && shown.includes("“Applied change” above"), shown);
    assert.equal(byId(env, "btn-apply"), undefined, "nothing to apply while it is applied");
    assert.equal(byId(env, "btn-reset"), undefined, "and Reset is the panel's");
    assert.equal(env.element('[data-role="op-headline"]').textContent, "Rolling out…");
});

test("a second Apply click while the first is on its way sends nothing", async () => {
    const env = await openApproved(applicationStatus(), { "POST /api/recommendations/declared/rec-live/apply": { status: 200, body: new Promise(() => {}) } });
    await byId(env, "btn-apply").listeners.click();
    byId(env, "apply-by").value = "asesh";
    const confirm = byId(env, "btn-confirm-apply");

    confirm.listeners.click();
    await settle();
    await confirm.listeners.click();          // the same button, once more, before the page has finished redrawing

    assert.equal(posts(env).length, 1);
});

test("Cancel puts the question away without sending anything", async () => {
    const env = await openApproved(applicationStatus());

    await byId(env, "btn-apply").listeners.click();
    await byId(env, "btn-cancel-application").listeners.click();

    assert.deepEqual(posts(env), []);
    assert.ok(byId(env, "btn-apply") && !byId(env, "btn-confirm-apply"));
});

test("when the server refuses, its reason is shown in the panel and the box reads the state again", async () => {
    const env = await openApproved(applicationStatus(), {
        "POST /api/recommendations/declared/rec-live/apply": { status: 409, body: { detail: { code: "APPROVAL_STALE", message: "The topology changed since this was approved." } } } });

    await byId(env, "btn-apply").listeners.click();
    byId(env, "apply-by").value = "asesh";
    await byId(env, "btn-confirm-apply").listeners.click();
    await settle();

    assert.ok(text(env.element('[data-role="op-message"]')).includes("The topology changed since this was approved."));
    assert.equal(env.element('[data-role="rec-operation"]').hidden, true, "the server recorded nothing, so there is no operation to show");
    assert.ok(byId(env, "btn-apply"), "the state was read again; it can still be tried");
    assert.ok(env.calls.filter(([m, u]) => m === "GET" && u === "/api/recommendations/declared/rec-live/application").length >= 2);
});

test("another application being active explains why Apply is not offered", async () => {
    const env = await openApproved(applicationStatus({ canApply: false, reason: "rec-other is still applied; reset it first." }));

    assert.equal(byId(env, "btn-apply"), undefined);
    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("rec-other is still applied; reset it first."));
});

test("an application the server holds is shown by the panel at load, whatever is selected, and reloading never applies it again", async () => {
    const stored = applicationStatus({ application: applicationOf("APPLIED"), rolloutComplete: false, canApply: false, canReset: true, recommendationId: "rec-from-before",
        operation: { direction: "apply", stage: "rollout", tone: "busy", headline: "Rolling out… waiting for the Deployments to settle on the new generation", nextAction: "wait",
            steps: [], inProgress: true, pollMs: 3000, connectionLost: false }, analysis: {}, states: null });
    const env = await openApproved(applicationStatus(), { "GET /api/recommendations/applications/current": { status: 200, body: stored } });
    await settle();

    assert.equal(env.element('[data-role="rec-operation"]').hidden, false);
    assert.ok(env.element('[data-role="op-headline"]').textContent.startsWith("Rolling out"));
    assert.deepEqual(posts(env), []);
});

test("a request that is only pending has no Apply section, and a frozen workflow uses the production routes", async () => {
    const pending = await openDeclared({ [D_APPROVAL]: { status: 200, headers: { "X-Evidence-Provenance": "research-declared" }, body: { ...approvedRecord, status: "PENDING_APPROVAL" } } });
    assert.ok(!text(pending.element('[data-role="approval-state-container"]')).includes("Apply to the connected cluster"));

    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") },
        "GET /api/recommendations/rec-live/approval": { status: 200, headers: { "X-Evidence-Provenance": "verified" }, body: approvedRecord },
        "GET /api/recommendations/rec-live/application": { status: 200, body: applicationStatus({ tier: "production" }) } });
    assert.ok(env.calls.some(([, u]) => u === "/api/recommendations/rec-live/application"));
    assert.ok(!calls_include(env, "/declared"));
});

const FAILURES = "GET /api/recommendations/live/failures?workflow_id=checkout&namespace=shop";
const overview = { scope: "Rankings stay separate", failures: [
    { failedNode: "w2", blastRadiusBefore: 100, singlePointOfFailure: true, candidates: 4, feasible: 3, ranked: 3,
        selected: { type: "REPLICA_INCREASE", service: "payment", predictedBlastRadius: 0, resourceCost: "MEDIUM" } },
] };

test("the failure panel controls exist outside the deferred page template", () => {
    const html = readFileSync(new URL("../../app/templates/components/recommendations.html", import.meta.url), "utf8");
    assert.ok(html.indexOf('data-role="rec-failures-load"') < html.indexOf('<template'), "button must be available when the script attaches its listener");
    assert.ok(html.indexOf('data-role="rec-failures-result"') < html.indexOf('<template'), "results must survive detail rerenders");
});

test("assess every worker displays the overview and Show loads that failure's detail", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
        [FAILURES]: { status: 200, body: overview },
        [`${DETAIL}&failed_node=w2`]: { status: 200, body: detailOf([cand()], "rec-live") },
    });
    await env.element('[data-role="rec-failures-load"]').listeners.click();
    const box = env.element('[data-role="rec-failures-result"]');
    assert.ok(text(box).includes("Rankings stay separate") && text(box).includes("w2"));
    const table = box.children.find(n => n.tag === "table");
    const button = table.children[1].children[0].children.at(-1).children[0];
    button.listeners.click();
    await new Promise(resolve => setTimeout(resolve, 0));
    assert.ok(calls_include(env, "/live/detail?workflow_id=checkout&namespace=shop&failed_node=w2"));
    assert.ok(text(box).includes("w2"), "opening a detail preserves the overview");
});

test("an assessment failure is explicit and the control can be retried", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([], null) },
        [FAILURES]: { status: 503, body: { detail: { code: "CLUSTER_CONNECTION_FAILED", message: "Cluster cannot be read" } } },
    });
    await env.element('[data-role="rec-failures-load"]').listeners.click();
    assert.ok(text(env.element('[data-role="rec-failures-result"]')).includes("Cluster cannot be read"));
    assert.equal(env.element('[data-role="rec-failures-load"]').disabled, false);
});

test("an assessment that completes after a cluster change cannot replace current evidence", async () => {
    let release;
    const pending = new Promise(resolve => { release = resolve; });
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([], null) },
        [FAILURES]: { status: 200, body: pending },
    });
    const request = env.element('[data-role="rec-failures-load"]').listeners.click();
    await env.windowListeners["resilience-cluster-changed"]();
    release(overview);
    await request;
    assert.equal(env.element('[data-role="rec-failures-result"]').children.length, 0);
});
