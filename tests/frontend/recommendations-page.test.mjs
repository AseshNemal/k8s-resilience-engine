// Integration test for the recommendations page: the real page script, context controller, API helper and display components run
// against a stubbed network and a fake DOM. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const SCRIPTS = ["api.js", "connection-state.js", "recommendation-render.js", "recommendation-context.js", "recommendation-portfolio.js", "recommendation-operation.js", "components/recommendations.js"].map((p) => [p, read(p)]);

function makeEnvironment(routes, storage) {
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
    const window = { document, localStorage: storage, addEventListener: (type, fn) => { windowListeners[type] = fn; } };
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
const PROOF = "GET /api/recommendations/validation-loop/proof?namespace=shop&workflowId=checkout";
const BASE = { "GET /_extension/cluster": { status: 200, body: { context: "docker-desktop" } }, "GET /api/recommendations/context": { status: 200, body: contextOf() } };

async function openPage(routes, storage) {
    const env = makeEnvironment({ ...BASE, ...routes }, storage);
    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));   // let the approval lookup that follows a render finish
    return env;
}

test("the connected cluster, the only application and the only workflow are used without typing anything", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    assert.deepEqual(env.calls.map((c) => `${c[0]} ${c[1]}`), ["GET /_extension/cluster", "GET /api/recommendations/applications/current", "GET /api/recommendations/context", DETAIL.slice(4).replace(/^/, "GET "), "GET /api/recommendations/validation-loop/proof?namespace=shop&workflowId=checkout", "GET /api/recommendations/rec-live/approval"]);
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
        "GET /api/recommendations/portfolio/detail?source=frozen&namespace=shop": { status: 200, body: { portfolio: { workflows: [], notEvaluated: [], note: "" }, candidates: [] } },
    });
    assert.equal(env.element('[data-role="rec-hint"]').textContent, "Pick a fix below, or choose one workflow above.", "the choice that is waiting is said under step 1");
    assert.ok(calls_include(env, "/portfolio/detail?source=frozen&namespace=shop"), "two frozen workflows are compared without being asked");
    assert.equal(env.element('[data-role="rec-workflow-select"]').attributes["data-attention"], "true");
    assert.equal(env.region.children.length, 0, "and the page does not repeat it in a block of its own");
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

    assert.ok(text(env.element('[data-role="approval-state-container"]')).includes("No approval requested"));
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

    assert.ok(text(env.element('[data-role="rec-candidate-list"]')).includes("Topology spread"));
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
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Declared workflow · research approval only");
    assert.ok(text(env.element('[data-role="declared-note"]')).includes("declared, see the research tier"), "the standing notice");
    assert.ok(!/not approvable|cannot be approved/i.test(env.element('[data-role="source-badge"]').textContent + text(env.element('[data-role="declared-note"]')).replace("declared, see the research tier", "")),
        "nothing on a declared page says it cannot be approved: the research tier exists, and production approval is simply not offered");
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
    assert.ok(shown.includes("Research approval requested") && shown.includes("Research approval:") && !shown.includes("PENDING_APPROVAL"), shown);
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

// ---- the stepped page: stepper, failure impact, evidence summary and the Simple / Research switch -------------------------

const stepStates = (env) => env.element('[data-role="rec-stepper"]').children.map((n) => n.attributes["data-state"]);
const IMPACT = { blastRadius: 100, survivability: 0, failedServices: ["payment"], failedWorkflows: ["checkout"] };

test("the stepper follows the page and the impact step shows the engine's result for the failure", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: { ...detailOf([cand()], "rec-live"), baselineImpact: IMPACT } },
        "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    // Step 4 is the one to do and the approval waits for it: it is not shown as the step to do while the change is unproven.
    assert.deepEqual(stepStates(env), ["done", "done", "done", "current", "waiting"]);
    assert.ok(text(env.element('[data-role="rec-impact"]')).includes("If w1 fails"));
    assert.ok(text(env.element('[data-role="rec-impact"]')).includes("The workflow does not survive"));
});

test("with missing evidence the stepper stops at the second step and nothing after it looks done", async () => {
    const missing = contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "missing"), ev("failureImpact", "missing")],
        nextActions: ["Freeze a workflow"] });
    const env = await openPage({ "GET /api/recommendations/context": { status: 200, body: missing } });

    assert.deepEqual(stepStates(env), ["current", "waiting", "waiting", "waiting", "waiting"], "no workflow is chosen yet, so the first step is the one to do");
    assert.equal(env.element('[data-role="rec-evidence-summary"]').textContent, "Evidence: 3 of 5 available");
    assert.equal(env.element('[data-role="rec-evidence-details"]').attributes["data-attention"], "true", "missing evidence is marked on its line, and the card says what to do");
});

test("a recommendation with nothing selected stops the stepper at the third step and offers nothing to approve", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: { ...detailOf([cand({ feasible: false, selected: false, approval: NOT_READY() })], null), baselineImpact: IMPACT } } });

    assert.deepEqual(stepStates(env), ["done", "done", "blocked", "waiting", "waiting"]);
});

test("the view switch changes only the view attribute, remembers the choice when it can, and works when storage is blocked", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    assert.equal(env.element('[data-role="rec-view-simple"]').attributes["aria-pressed"], "true");
    await env.element('[data-role="rec-view-research"]').listeners.click();     // storage is undefined in this environment: nothing throws
    assert.equal(env.element('[data-role="rec-flow"]').attributes["data-view"], "research");
    assert.equal(env.element('[data-role="rec-view-research"]').attributes["aria-pressed"], "true");
    assert.equal(env.element('[data-role="rec-view-simple"]').attributes["aria-pressed"], "false");
    await env.element('[data-role="rec-view-simple"]').listeners.click();
    assert.equal(env.element('[data-role="rec-flow"]').attributes["data-view"], "simple");
    assert.ok(!env.calls.some((c) => c[0] !== "GET" && !/approval|apply|reset/.test(c[1])), "switching view sends nothing");
});

// ---- step 1 says what to do next -----------------------------------------------------------------------------------------------------

// What the backend sends when only a declared workflow exists: no workflow is chosen for the person ("none"), which the page treats as blocked.
const onlyDeclared = () => declaredContext();
const declaredRoutes = (extra = {}) => ({
    "GET /api/recommendations/context": { status: 200, body: onlyDeclared() },
    "GET /api/recommendations/context?namespace=shop": { status: 200, body: onlyDeclared() },
    [D_DETAIL]: { status: 200, body: declaredBody(declaredCandidate()) },
    "GET /api/recommendations/declared/rec-live/approval": { status: 404, body: {} }, ...extra });

test("when only a declared workflow applies, the Workflow field is marked and the hint says the choice is the person's", async () => {
    const env = await openPage(declaredRoutes());

    const select = env.element('[data-role="rec-workflow-select"]');
    const hint = env.element('[data-role="rec-hint"]');
    assert.equal(select.attributes["data-attention"], "true");
    assert.equal(select.attributes["aria-describedby"], "rec-next-hint");
    assert.equal(hint.hidden, false);
    assert.ok(hint.textContent.includes("Choose a workflow to continue") && hint.textContent.includes("never picked for you"));
    assert.equal(env.element('[data-role="rec-namespace-select"]').attributes["data-attention"], undefined, "only the field to use next is marked");
    assert.ok(!calls_include(env, "/declared/detail"), "a declared workflow is never chosen for the person");
});

test("choosing the workflow clears the mark and the hint", async () => {
    const env = await openPage(declaredRoutes());
    const select = env.element('[data-role="rec-workflow-select"]');

    select.value = "declared:checkout";
    await select.listeners.change();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.ok(calls_include(env, "/declared/detail?workflow_id=checkout&namespace=shop"));
    assert.equal(select.attributes["data-attention"], undefined);
    assert.equal(env.element('[data-role="rec-hint"]').hidden, true);
    assert.equal(env.element('[data-role="rec-hint"]').textContent, "");
});

test("a page that is ready, or blocked for lack of evidence, marks nothing", async () => {
    const ready = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });
    assert.equal(ready.element('[data-role="rec-workflow-select"]').attributes["data-attention"], undefined);
    assert.equal(ready.element('[data-role="rec-hint"]').hidden, true);

    const none = contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "missing"), ev("failureImpact", "missing")] });
    const blocked = await openPage({ "GET /api/recommendations/context": { status: 200, body: none } });
    assert.equal(blocked.element('[data-role="rec-workflow-select"]').attributes["data-attention"], undefined);
    assert.equal(blocked.element('[data-role="rec-hint"]').hidden, true);
});

test("the failure dropdown's empty choice is short, and its title keeps the full meaning", async () => {
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([cand()], "rec-live") }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} } });

    const node = env.element('[data-role="rec-node-select"]');
    assert.equal(node.children[0].textContent, "Worst case node");
    assert.ok(node.attributes.title.includes("whose loss hurts most"));
});

// ---- less text: no repeated blocks, short notices with the full text kept ------------------------------------------------------------------

test("with only a declared workflow to choose, the page shows no error block: the hint under step 1 is the whole message", async () => {
    const env = await openPage(declaredRoutes());

    assert.equal(env.region.children.length, 0, "no red card repeating the evidence list");
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Choose to continue");
    assert.equal(env.element('[data-role="rec-hint"]').hidden, false);
});

test("the declared notice is one short line, with the full explanation kept for the research view and as a tooltip", async () => {
    const env = await openDeclared();

    const note = env.element('[data-role="declared-note"]');
    assert.equal(note.hidden, false);
    assert.equal(note.children[0].textContent, "Declared workflow, not observed. It can be approved for research validation on a test cluster only; production approval is not available for it.");
    assert.match(note.children[1].className, /rec-research-only/);
    assert.ok(note.children[1].textContent.includes("declared, see the research tier"));
    assert.ok(note.attributes.title.includes("declared, see the research tier"));
    assert.equal(env.element('[data-role="rec-outcome"]').attributes["data-declared"], "true", "the repeated outcome sentence is hidden in the plain view");
});

test("the validation note is one short line and keeps the engine's full statement as its tooltip", async () => {
    const body = { ...detailOf([cand()], "rec-live"), validation: { available: false, reason: "No experiment has been run for this change." } };
    const env = await openPage({ [DETAIL]: { status: 200, body }, "GET /api/recommendations/rec-live/approval": { status: 404, body: {} },
        [PROOF]: { status: 200, body: { state: "NOT_RUN" } } });

    const note = env.element('[data-role="rec-validation-note"]');
    assert.equal(note.textContent, "Not proven on a test cluster.");
    assert.equal(note.attributes.title, "No experiment has been run for this change.");
    assert.equal(env.element('[data-role="rec-validation-callout"]').attributes["data-state"], "unverified");
    assert.equal(env.element(".candidate-card__validation-text").textContent, "Not proven on a test cluster.", "the lead card says the same thing");
});

test("a card carries a reason only when its own change cannot be put forward, not for the notice every declared card would repeat", async () => {
    const infeasible = cand({ id: "rec-a", feasible: false, selected: false, approval: NOT_READY("Not enough worker nodes."), predicted: { blastRadiusBefore: 100, blastRadiusAfter: 100, riskReduction: 0 } });
    const declaredOnly = cand({ id: "rec-b", feasible: true, selected: false, approval: NOT_READY("Declared: research tier only.") });
    const env = await openPage({ [DETAIL]: { status: 200, body: detailOf([infeasible, declaredOnly], null) } });

    const shown = text(env.element('[data-role="rec-candidate-list"]'));
    assert.ok(shown.includes("Not enough worker nodes."));
    assert.ok(!shown.includes("Declared: research tier only."));
});

// ---- one step at a time, and where the work stands --------------------------------------------------------------------------------

const flow = (env) => env.element('[data-role="rec-flow"]');
const stepButton = (env, number) => env.element('[data-role="rec-stepper"]').children[number - 1].children[0];
const captionOf = (env, number) => text(env.element('[data-role="rec-stepper"]').children[number - 1]);
const READY_ROUTES = (extra = {}) => ({ [DETAIL]: { status: 200, body: { ...detailOf([cand()], "rec-live"), baselineImpact: IMPACT } },
    "GET /api/recommendations/rec-live/approval": { status: 404, body: {} }, [PROOF]: { status: 200, body: { state: "NOT_RUN" } }, ...extra });

test("returning to Docker with the same cluster preserves the open step and sends no writes", async () => {
    const env = await openPage(READY_ROUTES({
        "GET /api/recommendations/context?namespace=shop&workflow_id=checkout": BASE["GET /api/recommendations/context"],
    }));
    stepButton(env, 3).listeners.click();
    const detailReads = env.calls.filter(([, url]) => url.includes("/live/detail")).length;
    await env.windowListeners["resilience-cluster-changed"]();
    assert.equal(flow(env).attributes["data-step"], "3");
    assert.equal(env.calls.filter(([, url]) => url.includes("/live/detail")).length, detailReads);
    assert.deepEqual(posts(env), []);
});

test("a recreated Recommendations page restores its open step and choices from fresh server evidence", async () => {
    const data = new Map();
    const storage = { getItem: (k) => data.get(k) || null, setItem: (k, v) => data.set(k, v), removeItem: (k) => data.delete(k) };
    const routes = READY_ROUTES({
        "GET /api/recommendations/context?namespace=shop&workflow_id=checkout": BASE["GET /api/recommendations/context"],
    });
    const first = await openPage(routes, storage);
    stepButton(first, 3).listeners.click();
    first.windowListeners.pagehide();
    const second = await openPage(routes, storage);
    assert.equal(flow(second).attributes["data-step"], "3");
    assert.equal(second.element('[data-role="rec-workflow-select"]').value, "checkout");
    assert.ok(calls_include(second, "/live/detail"));
    assert.deepEqual(posts(second), []);
});

test("a ready recommendation opens at the impact step, one step is on screen, and the stepper can open the others", async () => {
    const env = await openPage(READY_ROUTES());

    assert.equal(flow(env).attributes["data-step"], "2", "the work is at the impact; the last open step is not where the page lands");
    assert.equal(flow(env).attributes["data-ready"], "true");
    stepButton(env, 3).listeners.click();
    assert.equal(flow(env).attributes["data-step"], "3");
    stepButton(env, 5).listeners.click();
    assert.equal(flow(env).attributes["data-step"], "5", "the approval can be opened before the change is proven");
    assert.equal(stepButton(env, 5).attributes["aria-current"], "step");
});

test("a step the person opened stays open until what the page is about changes, then the page follows the work again", async () => {
    const env = await openPage(READY_ROUTES());
    stepButton(env, 3).listeners.click();

    await env.element('[data-role="reload-button"]').listeners.click();
    await settle();

    assert.equal(flow(env).attributes["data-step"], "2");
});

test("every button that says where it goes opens that step, and any other click does nothing", async () => {
    const env = await openPage(READY_ROUTES());
    const click = (target) => flow(env).listeners.click({ target });

    click({ closest: () => ({ getAttribute: () => "4" }) });
    assert.equal(flow(env).attributes["data-step"], "4");
    click({ closest: () => null });
    click({ closest: () => ({ getAttribute: () => "9" }) });
    assert.equal(flow(env).attributes["data-step"], "4", "a click that names no step, or a step that does not exist, changes nothing");
});

test("the approval is neither done nor the step to do while the change is unproven, and becomes the step to do once the server says it is proven", async () => {
    const unproven = await openPage(READY_ROUTES());
    assert.deepEqual(stepStates(unproven), ["done", "done", "done", "current", "waiting"]);
    assert.ok(captionOf(unproven, 4).includes("Not run yet") && captionOf(unproven, 5).includes("After step 4"));

    const proven = await openPage(READY_ROUTES({ [PROOF]: { status: 200, body: { state: "PROVEN", preliminary: true, target: { name: "kind-test" } } } }));
    assert.deepEqual(stepStates(proven), ["done", "done", "done", "done", "current"]);
    assert.equal(proven.element('[data-role="rec-validation-callout"]').attributes["data-state"], "proven");
    assert.equal(proven.element('[data-role="rec-validation-go"]').hidden, true, "nothing to go and prove");
    assert.ok(proven.element('[data-role="rec-validation-note"]').textContent.startsWith("Proven on kind-test"));
});

test("a proof of another change, a failed test and an unreadable answer are each said, and none counts as proven", async () => {
    const cases = [
        [{ state: "OTHER_PROVEN", target: { name: "kind-test" } }, "other", "A different change was proven on kind-test, not this one."],
        [{ state: "TESTED_NONE", target: { name: "kind-test" } }, "failed", "Tested on kind-test; no change was proven."],
    ];
    for (const [body, state, sentence] of cases) {
        const env = await openPage(READY_ROUTES({ [PROOF]: { status: 200, body } }));
        assert.equal(env.element('[data-role="rec-validation-callout"]').attributes["data-state"], state);
        assert.equal(env.element('[data-role="rec-validation-note"]').textContent, sentence);
        assert.equal(env.element('[data-role="rec-validation-go"]').hidden, false);
        assert.notEqual(stepStates(env)[3], "done");
    }
    const unread = await openPage(READY_ROUTES({ [PROOF]: { status: 500, body: {} } }));
    assert.equal(unread.element('[data-role="rec-validation-callout"]').attributes["data-state"], "unknown");
    assert.equal(unread.element('[data-role="rec-validation-note"]').textContent, "Whether this change was proven could not be read.");
    assert.notEqual(stepStates(unread)[3], "done", "an answer that could not be read is never a proof");
});

const production = (status, over = {}) => ({ id: 1, recommendationId: "rec-live", proposalVersion: "v1.0", status, requestedAt: "2026-10-20T08:00:00Z", requestedBy: "asesh",
    decidedAt: status === "PENDING_APPROVAL" ? null : "2026-10-21T08:00:00Z", decidedBy: status === "PENDING_APPROVAL" ? null : "sam", decisionNote: null,
    recommendationSnapshot: {}, yamlSnapshot: null, evaluationSnapshot: {}, ...over });
const recordRoutes = (record, proof) => READY_ROUTES({ "GET /api/recommendations/rec-live/approval": { status: 200, headers: { "X-Evidence-Provenance": "verified" }, body: record },
    "GET /api/recommendations/rec-live/application": { status: 200, body: { recommendationId: "rec-live", application: null, canApply: false, reason: "Writes are off.", tier: "production" } },
    [PROOF]: { status: 200, body: proof } });
const history = (env) => allNodes(env.element('[data-role="approval-state-container"]')).find((n) => /approval-history/.test(n.className || ""));

test("an approval recorded before the change was proven is kept and shown as an earlier approval, in the production tier's words, and not as validation", async () => {
    const env = await openPage(recordRoutes(production("APPROVED"), { state: "NOT_RUN" }));

    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("Approved") && !shown.includes("APPROVED") && shown.includes("Approved by sam on"), shown);
    assert.ok(shown.includes("Production approval, from live evidence."));
    assert.equal(history(env).hidden, false, "the record says it does not count as a validation");
    assert.ok(history(env).textContent.includes("does not count as one"));
    assert.deepEqual(stepStates(env), ["done", "done", "done", "current", "recorded"]);
    assert.ok(captionOf(env, 5).includes("Earlier approval"));
});

test("once the change is proven the earlier-approval note goes away and the record is just a record", async () => {
    const env = await openPage(recordRoutes(production("APPROVED"), { state: "PROVEN", target: { name: "kind-test" } }));

    assert.equal(history(env).hidden, true);
    assert.deepEqual(stepStates(env), ["done", "done", "done", "done", "current"]);
});

test("a pending request and a rejection are worded as what they are, never as raw status codes", async () => {
    const pending = await openPage(recordRoutes(production("PENDING_APPROVAL"), { state: "NOT_RUN" }));
    const shownPending = text(pending.element('[data-role="approval-state-container"]'));
    assert.ok(shownPending.includes("Approval requested") && shownPending.includes("Approval requested by asesh on") && !shownPending.includes("PENDING_APPROVAL"));
    assert.ok(history(pending).textContent.startsWith("This request was made without"), "a request is not yet a decision");

    const rejected = await openPage(recordRoutes(production("REJECTED"), { state: "NOT_RUN" }));
    assert.ok(text(rejected.element('[data-role="approval-state-container"]')).includes("Rejected by sam on"));
});

test("a declared workflow's record is worded as research approval, and the production wording never appears beside it", async () => {
    const env = await openApproved(applicationStatus());

    const shown = text(env.element('[data-role="approval-state-container"]'));
    assert.ok(shown.includes("Approved for research"), shown);
    assert.ok(shown.includes("Research approval: the workflow was declared, not observed."));
    assert.ok(!shown.includes("Production approval") && !/not approvable/i.test(shown));
});

test("a change applied on the cluster is announced on the other steps and left to its own panel on step 5", async () => {
    const stored = applicationStatus({ application: applicationOf("APPLIED"), rolloutComplete: false, canApply: false, canReset: true, recommendationId: "rec-live",
        operation: { direction: "apply", stage: "rollout", tone: "busy", headline: "Rolling out…", nextAction: "wait", steps: [], inProgress: false, pollMs: 3000, connectionLost: false }, analysis: {}, states: null });
    const env = await openApproved(applicationStatus(), { "GET /api/recommendations/applications/current": { status: 200, body: stored } });
    await settle();

    stepButton(env, 3).listeners.click();
    assert.equal(env.element('[data-role="rec-applied-notice"]').hidden, false);
    assert.ok(env.element('[data-role="rec-applied-text"]').textContent.includes("db"));
    stepButton(env, 5).listeners.click();
    assert.equal(env.element('[data-role="rec-applied-notice"]').hidden, true, "step 5 shows the change itself");
});

test("nothing from an earlier recommendation is left in the research tabs when the evidence goes missing", async () => {
    const missing = contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "missing"), ev("failureImpact", "missing")] });
    let answer = contextOf();
    const env = await openPage({ ...READY_ROUTES(), "GET /api/recommendations/context": () => ({ status: 200, body: answer }),
        "GET /api/recommendations/context?namespace=shop": () => ({ status: 200, body: answer }) });
    assert.ok(env.element('[data-role="rec-raw-json"]').textContent.includes("rec-live"));
    assert.ok(text(env.element('[data-role="rec-candidate-table-body"]')).includes("Topology spread"));

    answer = missing;
    env.windowListeners["resilience-cluster-changed"]();
    await settle();
    await settle();

    assert.equal(env.element('[data-role="rec-raw-json"]').textContent, "Nothing loaded yet.");
    assert.ok(text(env.element('[data-role="rec-candidate-table-body"]')).includes("No candidates to rank."));
    assert.equal(env.element('[data-metric="rec-feasible-count"]').textContent, "0");
});


// ---- across all workflows: a fix is chosen in the comparison, then reviewed under ONE workflow ----------------------------------------------------

const second = { ...declaredWf, workflowId: "browse" };
const twoDeclared = () => contextOf({ workflows: [], workflowId: null, workflowChoice: "none", ready: false, declaredWorkflows: [declaredWf, second],
    evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "missing"), ev("failureImpact", "missing")] });
const PORTFOLIO = "GET /api/recommendations/portfolio/detail?source=declared&namespace=shop";
const portfolioCandidate = (service = "payment", type = "TOPOLOGY_SPREAD") => ({
    id: `rec-all-workflows-${service}`, type, service, feasible: true, selected: true, ranking: { rank: 1 }, cost: { resource: "LOW" }, feasibility: { explanation: null },
    workflows: { protected: ["checkout"], exposed: ["browse"], exposedAt: { browse: ["w2"] } },
    coverage: { worstCaseBefore: 75, worstCaseAfter: 25, protectsEveryWorkflow: false },
    parts: [{ candidateId: `rec-all-workflows-${service}`, service, type, protects: ["checkout"] }],
});
const portfolioBody = (candidate = portfolioCandidate()) => ({
    portfolio: { workflows: [{ workflowId: "checkout", criticality: 0.9 }, { workflowId: "browse", criticality: 0.3 }], notEvaluated: [], note: "Approval is for one workflow." },
    candidates: [candidate],
});
const findAll = (node, predicate, out = []) => { if (predicate(node)) out.push(node); (node.children || []).forEach((child) => findAll(child, predicate, out)); return out; };

async function openComparison(extraRoutes = {}) {
    return openPage({
        "GET /api/recommendations/context": { status: 200, body: twoDeclared() },
        "GET /api/recommendations/context?namespace=shop": { status: 200, body: twoDeclared() },
        [PORTFOLIO]: { status: 200, body: portfolioBody() },
        [D_DETAIL]: { status: 200, body: declaredBody(declaredCandidate()) },
        "GET /api/recommendations/declared/rec-live/approval": { status: 404, body: {} }, ...extraRoutes });
}

async function reviewFor(env, workflowId) {
    const root = env.element('[data-role="rec-portfolio"]');
    findAll(root, (n) => n.tag === "button" && n.textContent === "Use this fix")[0].listeners.click();
    const select = findAll(root, (n) => n.tag === "select")[0];
    select.value = workflowId;
    findAll(root, (n) => n.tag === "button" && n.textContent === "Review")[0].listeners.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    await new Promise((resolve) => setTimeout(resolve, 0));
}

test("with two usable workflows the comparison across all of them is asked for, once, and nothing is chosen for the person", async () => {
    const env = await openComparison();

    assert.equal(env.calls.filter(([, url]) => url === PORTFOLIO.slice(4)).length, 1);
    assert.equal(env.element('[data-role="rec-portfolio"]').hidden, false);
    assert.ok(text(env.element('[data-role="rec-portfolio"]')).includes("Across all workflows"));
    assert.ok(!calls_include(env, "/declared/detail"), "no workflow review starts until the person chooses a fix and a workflow");
    assert.ok(env.element('[data-role="rec-hint"]').textContent.startsWith("Pick a fix below"));
    assert.ok(env.calls.every(([method]) => method === "GET"), "the comparison only reads");
});

test("with one workflow there is no comparison to show and none is asked for", async () => {
    const env = await openDeclared();

    assert.ok(!calls_include(env, "/portfolio/detail"));
});

test("choosing a fix and a workflow reviews that fix under that workflow, through the ordinary declared route", async () => {
    const env = await openComparison();

    await reviewFor(env, "checkout");

    assert.ok(calls_include(env, "/declared/detail?workflow_id=checkout&namespace=shop"));
    assert.equal(env.element('[data-role="source-badge"]').textContent, "Declared workflow · research approval only");
    assert.ok(text(env.element('[data-role="rec-outcome"]')).includes("You chose Topology spread on payment from the all-workflows comparison"));
    assert.ok(env.calls.every(([method]) => method === "GET"), "choosing a fix applies and approves nothing");
});

test("a chosen change this workflow does not offer is said so, and its own top-ranked change is shown", async () => {
    const env = await openComparison({ [PORTFOLIO]: { status: 200, body: portfolioBody(portfolioCandidate("cache")) } });

    await reviewFor(env, "checkout");

    assert.ok(text(env.element('[data-role="rec-outcome"]')).includes("The change you chose (Topology spread on cache) is not an option for this workflow"));
});

test("a change that ranks below the workflow's own top one says approving it needs a written justification", async () => {
    const top = declaredCandidate({ id: "rec-top", service: "web", selected: true });
    const chosen = declaredCandidate({ id: "rec-payment", service: "payment", selected: false, score: 0.5, approval: { ready: false, reason: "declared", requiresJustification: true, declared: true } });
    const body = { ...declaredBody(top), candidates: [top, chosen], report: reportOf([top, chosen], "rec-top") };
    const env = await openComparison({ [D_DETAIL]: { status: 200, body }, "GET /api/recommendations/declared/rec-payment/approval": { status: 404, body: {} } });

    await reviewFor(env, "checkout");

    assert.ok(text(env.element('[data-role="rec-outcome"]')).includes("another change ranks first, so approving this one needs a written justification"));
});

test("choosing a workflow by hand drops the fix chosen in the comparison", async () => {
    const env = await openComparison({ "GET /api/recommendations/context?namespace=shop&workflow_id=": { status: 200, body: twoDeclared() } });
    await reviewFor(env, "checkout");

    const select = env.element('[data-role="rec-workflow-select"]');
    select.value = "declared:checkout";
    await select.listeners.change();
    await new Promise((resolve) => setTimeout(resolve, 0));

    assert.ok(!text(env.element('[data-role="rec-outcome"]')).includes("from the all-workflows comparison"));
});
