// Logic tests for the Recommendations page's automatic selection (recommendation-context.js): which cluster, application
// and frozen workflow it uses, what it says is missing, and that nothing chosen on one cluster reaches another.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-context.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, URLSearchParams, Array, Object, JSON, Promise, String, Number, Error, Boolean });
const C = window.ResilienceRecommendationContext;

test("all-workflows mode needs no workflow choice and requests one aggregate evaluation", async () => {
    const body = context({ workflows: [workflow("a"), workflow("b")], workflowId: null, workflowChoice: "choose", ready: false });
    const { env, calls } = makeEnv({ "/api/recommendations/context": body, "/api/recommendations/live/detail": detailBody() });
    env.allWorkflows = true;
    const c = C.createController(env);
    const v = await c.start();
    assert.equal(v.status, "ready");
    assert.equal(v.workflowId, "all-workflows");
    assert.equal(c.selection().allWorkflows, true);
    assert.match(calls.at(-1), /all_workflows=true/);
});

test("all-workflows mode keeps declared evidence in the research tier and never mixes it with frozen workflows", async () => {
    const body = context({ workflows: [], declaredWorkflows: [{ workflowId: "declared-a", services: ["a"], applicable: true }],
        workflowId: null, workflowChoice: "none", ready: false });
    const { env, calls } = makeEnv({ "/api/recommendations/context": body, "/api/recommendations/declared/detail": detailBody() });
    env.allWorkflows = true;
    const v = await C.createController(env).start();
    assert.equal(v.workflowKind, "declared");
    assert.match(calls.at(-1), /\/declared\/detail\?.*all_workflows=true/);
    assert.ok(!v.evidence.some((e) => e.id === "workflow"), "a missing frozen workflow is not an error for a ready research evaluation");
});

function selectionStore() {
    const data = new Map();
    return { getItem: (k) => data.get(k) || null, setItem: (k, v) => data.set(k, v), removeItem: (k) => data.delete(k) };
}

test("routine cluster notifications do not publish or reload a ready recommendation", async () => {
    const { env, calls, updates } = makeEnv({ "/api/recommendations/context": context(), "/api/recommendations/live/detail": detailBody() });
    const c = C.createController(env);
    await c.start();
    const count = updates.length;
    const reads = calls.filter((u) => u.includes("/detail")).length;
    assert.equal(await c.connectionChanged(), false);
    assert.equal(await c.connectionChanged(), false);
    assert.equal(updates.length, count, "no loading render clears the page");
    assert.equal(calls.filter((u) => u.includes("/detail")).length, reads);
});

test("a reload restores choices but fetches fresh evidence, and a replaced cluster drops them", async () => {
    const storage = selectionStore();
    const routes = { "/api/recommendations/context": context(), "/api/recommendations/live/detail": detailBody() };
    const first = makeEnv(routes);
    first.env.storage = storage;
    const c = C.createController(first.env);
    await c.start();
    await c.selectFailedNode("w2");
    const second = makeEnv(routes);
    second.env.storage = storage;
    const restored = await C.createController(second.env).start();
    assert.equal(restored.failedNode, "w2");
    assert.match(second.calls.at(-1), /failed_node=w2/);
    assert.equal(second.calls.length, 2, "selection storage never replaces live reads");

    const replaced = makeEnv({ ...routes, "/api/recommendations/context": context({ cluster: { state: "available", fingerprint: "cluster-B" } }) });
    replaced.env.storage = storage;
    const newCluster = await C.createController(replaced.env).start();
    assert.equal(newCluster.failedNode, "");
    assert.ok(replaced.calls.filter((u) => u.includes("/detail")).every((u) => !u.includes("failed_node=w2")));
});

test("same context name with a new cluster fingerprint reloads without old choices", async () => {
    let body = context();
    const { env, calls } = makeEnv({ "/api/recommendations/context": () => body, "/api/recommendations/live/detail": detailBody() });
    const c = C.createController(env);
    await c.start();
    await c.selectFailedNode("w2");
    body = context({ cluster: { state: "available", fingerprint: "cluster-B" } });
    assert.equal(await c.connectionChanged(), true);
    assert.equal(c.view().failedNode, "");
    assert.ok(!calls.at(-1).includes("failed_node=w2"));
});

test("bad or blocked selection storage does not prevent live loading", async () => {
    for (const storage of [
        { getItem: () => "{broken", setItem() {} },
        { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } },
    ]) {
        const { env } = makeEnv({ "/api/recommendations/context": context(), "/api/recommendations/live/detail": detailBody() });
        env.storage = storage;
        assert.equal((await C.createController(env).start()).status, "ready");
    }
});
const plain = (value) => JSON.parse(JSON.stringify(value));

const workflow = (id, extra = {}) => ({ workflowId: id, freezeId: "f1", frozenAt: "2026-10-20T10:00:00Z", services: ["a", "b"], applicable: true, reason: null, ...extra });
const ev = (id, state, extra = {}) => ({ id, label: id, state, detail: `${id} ${state}`, nextAction: null, ...extra });
function context(over = {}) {
    return {
        cluster: { state: "available", fingerprint: "cluster-A" },
        namespaces: [{ namespace: "bookinfo", services: ["details", "productpage", "ratings", "reviews"] }],
        namespace: "bookinfo", namespaceChoice: "only-application",
        workflows: [workflow("bookinfo-productpage")], workflowId: "bookinfo-productpage", workflowChoice: "only-applicable",
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"), ev("workflow", "available"), ev("failureImpact", "pending")],
        nextActions: [], ready: true, readOnly: true, ...over,
    };
}
const detailBody = (over = {}) => ({
    outcome: { state: "CANDIDATES_NOT_READY", message: "none can be approved" },
    report: { workflowId: "bookinfo-productpage", recommendations: [], selectedRecommendationId: null },
    evidence: { blastRadiusBefore: 100, failedNode: "w1", failedNodeChosen: "highest blast radius", workflowId: "bookinfo-productpage" },
    candidates: [{ id: "c1", service: "ratings", feasible: false, approval: { ready: false, reason: "not feasible" } }],
    currentDeployment: { nodes: [{ name: "w1" }, { name: "w2" }], services: [] }, ...over,
});
const codedError = (code, message, details) => Object.assign(new Error(message), { code, details: details || null, status: 409 });

// routes: url -> body | Error | function(url) -> body. Records every request.
function makeEnv(routes, identity = "docker-desktop") {
    const calls = [];
    const updates = [];
    const state = { identity };
    const env = {
        fetchJson: async (url) => {
            calls.push(url);
            const route = routes[url] ?? routes[url.split("?")[0]];
            if (route === undefined) throw new Error(`unexpected request ${url}`);
            const value = typeof route === "function" ? route(url) : route;
            if (value instanceof Error) throw value;
            return value;
        },
        clusterIdentity: async () => state.identity,
        onUpdate: (v) => updates.push(v.status),
    };
    return { env, calls, updates, state };
}

test("the only application and the only applicable workflow are used without typing anything", async () => {
    const { env, calls } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": detailBody(),
    });
    const c = C.createController(env);

    const view = await c.start();

    assert.equal(view.status, "ready");
    assert.equal(view.namespace, "bookinfo");
    assert.equal(view.workflowId, "bookinfo-productpage");
    assert.deepEqual(plain(c.selection()), { workflowId: "bookinfo-productpage", namespace: "bookinfo", failedNode: "", workflowKind: "frozen" });
    assert.deepEqual(calls, ["/api/recommendations/context", "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo"]);
    assert.deepEqual(plain(view.failedNodes), ["w1", "w2"], "the failed-node dropdown lists the cluster's own nodes");
});

test("the workflow dropdown lists every frozen workflow and marks the ones that do not apply", async () => {
    const body = context({
        workflows: [workflow("a"), workflow("b", { applicable: false, reason: "Services not found in bookinfo: db." })],
        workflowId: "a", workflowChoice: "only-applicable",
    });
    const { env } = makeEnv({ "/api/recommendations/context": body, "/api/recommendations/context?namespace=bookinfo&workflow_id=a": body,
        "/api/recommendations/live/detail?workflow_id=a&namespace=bookinfo": detailBody() });
    const view = await C.createController(env).start();

    assert.deepEqual(plain(view.workflows.map((w) => [w.workflowId, w.applicable])), [["a", true], ["b", false]]);
    assert.match(view.workflows[0].label, /^a · frozen 2026-10-20 · 2 services$/);
    assert.match(view.workflows[1].label, /not usable here: Services not found in bookinfo: db\./);
});

test("with several applicable workflows nothing is guessed: you choose, and only then is a recommendation requested", async () => {
    const several = context({ workflows: [workflow("a"), workflow("b")], workflowId: null, workflowChoice: "choose", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("workflow", "available"), ev("failureImpact", "missing")] });
    const chosen = context({ workflows: [workflow("a"), workflow("b")], workflowId: "b", workflowChoice: "requested" });
    const { env, calls } = makeEnv({
        "/api/recommendations/context": several,
        "/api/recommendations/context?namespace=bookinfo": several,
        "/api/recommendations/context?namespace=bookinfo&workflow_id=b": chosen,
        "/api/recommendations/live/detail?workflow_id=b&namespace=bookinfo": detailBody(),
    });
    const c = C.createController(env);

    const first = await c.start();
    assert.equal(first.status, "needs-input");
    assert.ok(!calls.some((u) => u.includes("/live/detail")), "no recommendation before a workflow is chosen");

    const second = await c.selectWorkflow("b");
    assert.equal(second.status, "ready");
    assert.equal(second.workflowId, "b");
});

test("several applications: none is chosen for you", async () => {
    const ambiguous = context({ namespaces: [{ namespace: "bookinfo", services: [] }, { namespace: "shop", services: [] }], namespace: null,
        namespaceChoice: "ambiguous", workflowId: null, ready: false,
        evidence: [ev("cluster", "available"), ev("application", "missing", { nextAction: "Choose the application to recommend for." })] });
    const { env, calls } = makeEnv({ "/api/recommendations/context": ambiguous });
    const view = await C.createController(env).start();

    assert.equal(view.status, "needs-input");
    assert.equal(view.namespace, "");
    assert.equal(calls.length, 1);
});

test("a missing frozen workflow says which evidence is missing and the exact next action, and asks for no recommendation", async () => {
    const missing = context({ workflows: [], workflowId: null, workflowChoice: "none", ready: false,
        evidence: [ev("cluster", "available"), ev("application", "available"), ev("topology", "available"),
            ev("workflow", "missing", { nextAction: "Open Workflow dependencies, assess ..., approve ... and freeze its graph" }), ev("failureImpact", "missing")],
        nextActions: ["Open Workflow dependencies, assess ..., approve ... and freeze its graph"] });
    const { env, calls } = makeEnv({ "/api/recommendations/context": missing, "/api/recommendations/context?namespace=bookinfo": missing });
    const view = await C.createController(env).start();

    assert.equal(view.status, "blocked");
    const workflowItem = view.evidence.find((e) => e.id === "workflow");
    assert.equal(workflowItem.state, "missing");
    assert.match(workflowItem.nextAction, /freeze its graph/);
    assert.deepEqual(plain(view.workflows), []);
    assert.equal(view.detail, null, "no recommendation is shown or invented");
    assert.ok(!calls.some((u) => u.includes("/live/detail")));
});

test("missing failure-impact evidence (an incomplete topology) names the services and what to do", async () => {
    const error = codedError("TOPOLOGY_INCOMPLETE", "The topology is not complete enough for the services this workflow depends on.",
        { services: [{ service: "ratings", reasons: ["NO_PLACEMENT_OBSERVED"] }] });
    const { env } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": error,
    });
    const view = await C.createController(env).start();

    assert.equal(view.status, "blocked");
    assert.equal(view.detail, null);
    assert.equal(view.error.code, "TOPOLOGY_INCOMPLETE");
    assert.deepEqual(plain(view.error.lines), ["ratings: NO_PLACEMENT_OBSERVED"]);
    assert.match(view.error.nextAction, /Deployment topology/);
    const item = view.evidence.find((e) => e.id === "failureImpact");
    assert.equal(item.state, "incomplete");
    assert.match(item.detail, /ratings: NO_PLACEMENT_OBSERVED/);
});

test("each coded failure has its own next action and a bad request is not retryable", () => {
    for (const code of ["WORKFLOW_EVIDENCE_UNAVAILABLE", "TOPOLOGY_INCOMPLETE", "RECOMMENDATION_EVIDENCE_UNAVAILABLE", "CLUSTER_NOT_CONNECTED"]) {
        assert.ok(C.explainError(codedError(code, "m")).nextAction, code);
    }
    assert.equal(C.explainError(codedError("INVALID_RECOMMENDATION_REQUEST", "bad node")).retryable, false);
    assert.equal(C.explainError(new Error("network")).nextAction, null, "an unknown failure invents no advice");
});

test("feasible and infeasible candidates are shown exactly as the server judged them", async () => {
    const mixed = detailBody({
        outcome: { state: "READY", message: "1 candidate can be put to approval" },
        candidates: [
            { id: "ok", service: "db", feasible: true, approval: { ready: true, reason: null } },
            { id: "no", service: "ratings", feasible: false, approval: { ready: false, reason: "single replica" } },
        ],
    });
    const { env } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": mixed,
    });
    const view = await C.createController(env).start();

    assert.deepEqual(plain(view.detail.candidates.map((c) => [c.id, c.approval.ready])), [["ok", true], ["no", false]]);
    const item = view.evidence.find((e) => e.id === "failureImpact");
    assert.equal(item.state, "available");
    assert.match(item.detail, /Predicted blast radius 100% if w1 fails/);
});

test("choosing a failed node asks again for that node and nothing else changes", async () => {
    const { env, calls } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": detailBody(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo&failed_node=w2": detailBody({ evidence: { blastRadiusBefore: 40, failedNode: "w2", failedNodeChosen: "requested" } }),
    });
    const c = C.createController(env);
    await c.start();
    await c.selectFailedNode("w2");

    assert.equal(c.view().evidence.find((e) => e.id === "failureImpact").detail.includes("40%"), true);
    assert.equal(calls.at(-1), "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo&failed_node=w2");
    assert.equal(c.selection().failedNode, "w2");
});

// ---- cluster switching and stale answers ----

test("when the connected cluster changes, nothing chosen on the old one is sent to the new one", async () => {
    const oldCtx = context({ namespace: "bookinfo", workflowId: "wf-old", workflows: [workflow("wf-old")] });
    const newCtx = context({ cluster: { state: "available", fingerprint: "cluster-B" }, namespaces: [{ namespace: "shop", services: ["web"] }],
        namespace: "shop", workflows: [workflow("wf-new")], workflowId: "wf-new" });
    const { env, calls, state } = makeEnv({
        "/api/recommendations/context": (url) => (state.identity === "docker-desktop" ? oldCtx : newCtx),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=wf-old": oldCtx,
        "/api/recommendations/live/detail?workflow_id=wf-old&namespace=bookinfo": detailBody(),
        "/api/recommendations/live/detail?workflow_id=wf-new&namespace=shop": detailBody({ evidence: { blastRadiusBefore: 10, failedNode: "x1", failedNodeChosen: "highest blast radius" } }),
    });
    const c = C.createController(env);
    await c.start();
    assert.equal(c.selection().namespace, "bookinfo");

    state.identity = "kind-other";
    const view = await c.clusterChanged();

    assert.equal(view.namespace, "shop");
    assert.equal(view.workflowId, "wf-new");
    const afterSwitch = calls.slice(calls.indexOf("/api/recommendations/live/detail?workflow_id=wf-old&namespace=bookinfo") + 1);
    assert.ok(!afterSwitch.some((u) => u.includes("wf-old") || u.includes("namespace=bookinfo")), `old choices were sent to the new cluster: ${afterSwitch}`);
});

test("an answer that arrives for an older request is dropped", async () => {
    let releaseOld;
    const slow = new Promise((resolve) => { releaseOld = () => resolve(context({ namespace: "bookinfo", workflowId: null, ready: false, workflowChoice: "none" })); });
    const fresh = context({ namespace: "shop", namespaces: [{ namespace: "shop", services: [] }], workflows: [workflow("wf-new")], workflowId: "wf-new" });
    let n = 0;
    const calls = [];
    const env = {
        fetchJson: async (url) => {
            calls.push(url);
            if (url.includes("/live/detail")) return detailBody();
            n += 1;
            return n === 1 ? slow : fresh;      // the first context answer is slow, the second is quick
        },
        clusterIdentity: async () => "docker-desktop",
        onUpdate: () => {},
    };
    const c = C.createController(env);
    const first = c.start();                    // slow
    await new Promise((r) => setTimeout(r, 0));
    const second = c.selectNamespace("shop");   // newer
    await second;
    releaseOld();
    await first;

    assert.equal(c.view().namespace, "shop", "the late, older answer must not overwrite the newer choice");
    assert.equal(c.view().workflowId, "wf-new");
});

test("the same connection reaching a different cluster resets the choices and requests nothing for the old ones", async () => {
    let fingerprint = "cluster-A";
    const answer = (ns, wf) => context({ cluster: { state: "available", fingerprint }, namespaces: [{ namespace: ns, services: [] }], namespace: ns, workflowId: wf, workflows: [workflow(wf)] });
    const { env, calls } = makeEnv({
        // what the server says depends on the cluster behind the connection, and on whether choices were sent
        "/api/recommendations/context": () => (fingerprint === "cluster-A" ? answer("bookinfo", "wf-a") : answer("shop", "wf-z")),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=wf-a": () => answer("bookinfo", "wf-a"),
        "/api/recommendations/live/detail?workflow_id=wf-a&namespace=bookinfo": detailBody(),
        "/api/recommendations/live/detail?workflow_id=wf-z&namespace=shop": detailBody(),
    });
    const c = C.createController(env);
    await c.start();
    const before = calls.length;

    fingerprint = "cluster-B";   // the connection is unchanged, but it now reaches another cluster
    const view = await c.reload();

    assert.equal(view.namespace, "shop");
    assert.equal(view.workflowId, "wf-z");
    assert.equal(view.status, "ready");
    const after = calls.slice(before);
    assert.ok(!after.some((u) => u.includes("/live/detail?workflow_id=wf-a")), `the old workflow was requested on the new cluster: ${after}`);
    assert.equal(after.at(-1), "/api/recommendations/live/detail?workflow_id=wf-z&namespace=shop");
});

test("only the two read endpoints are ever called, with GET, and nothing is stored", async () => {
    const { env, calls } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": detailBody(),
    });
    await C.createController(env).start();

    assert.ok(calls.every((u) => u.startsWith("/api/recommendations/context") || u.startsWith("/api/recommendations/live/detail")));
    assert.ok(!/localStorage|sessionStorage|indexedDB|document\.cookie|method\s*:\s*["'](POST|PUT|PATCH|DELETE)/.test(source));
});

test("a context answer that is not in the expected shape is a failure, never an empty cluster", async () => {
    const { env } = makeEnv({ "/api/recommendations/context": { nope: true } });
    const view = await C.createController(env).start();

    assert.equal(view.status, "blocked");
    assert.deepEqual(plain(view.namespaces), []);
    assert.ok(view.error);
});


// ---- declared workflows: Member 4's own source when Component 1 has no frozen workflow -----------------------------------------------------

const declared = (id, extra = {}) => ({ workflowId: id, declaredBy: "asesh", services: ["a", "b"], applicable: true, reason: null, declared: true, approvable: false, ...extra });
const noFrozen = (over = {}) => context({ workflows: [], workflowId: null, workflowChoice: "none", ready: false, declaredWorkflows: [declared("checkout")], ...over });
const DECLARED_DETAIL = "/api/recommendations/declared/detail?workflow_id=checkout&namespace=bookinfo";
const declaredBody = () => detailBody({
    outcome: { state: "DECLARED_NOT_APPROVABLE", message: "declared, not approvable" },
    candidates: [{ id: "c1", service: "reviews", feasible: true, approval: { ready: false, reason: "declared", declared: true } }],
    declared: { workflowId: "checkout", observed: false, approvable: false, notApprovableBecause: "declared, not approvable" },
});

test("a declared workflow is offered, labelled, and never chosen for you even when it is the only one", async () => {
    const { env, calls } = makeEnv({ "/api/recommendations/context": noFrozen(), "/api/recommendations/context?namespace=bookinfo": noFrozen() });
    const c = C.createController(env);

    const view = await c.start();

    assert.equal(view.status, "blocked", "no frozen workflow means missing evidence, however many declared ones exist");
    assert.deepEqual(calls, ["/api/recommendations/context"], "no recommendation was requested");
    const [option] = plain(view.workflows);
    assert.equal(option.value, "declared:checkout");
    assert.equal(option.declared, true);
    assert.match(option.label, /declared, research approval only/);
    assert.doesNotMatch(option.label, /not approvable/);
    assert.equal(view.workflowId, "");
});

test("choosing a declared workflow asks the declared route, never the frozen one, and says so", async () => {
    const { env, calls } = makeEnv({
        "/api/recommendations/context": noFrozen(), "/api/recommendations/context?namespace=bookinfo": noFrozen(),
        [DECLARED_DETAIL]: declaredBody(),
    });
    const c = C.createController(env);
    await c.start();

    const view = await c.selectWorkflow("declared:checkout");

    assert.equal(view.status, "ready");
    assert.equal(view.workflowKind, "declared");
    assert.equal(view.workflowValue, "declared:checkout");
    assert.equal(view.workflowId, "checkout");
    assert.deepEqual(plain(c.selection()), { workflowId: "checkout", namespace: "bookinfo", failedNode: "", workflowKind: "declared" });
    assert.ok(calls.includes(DECLARED_DETAIL));
    assert.ok(!calls.some((u) => u.includes("/live/detail")), "the frozen route is never asked about a declared workflow");
    assert.ok(!calls.some((u) => u.startsWith("/api/recommendations/context") && u.includes("workflow_id=")), "a declared id is never sent to the context as a frozen one");
    assert.equal(plain(view.detail.candidates[0].approval).ready, false, "the server's verdict, shown as it came");
});

test("a frozen and a declared workflow with the same id are never confused", async () => {
    const both = context({ declaredWorkflows: [declared("bookinfo-productpage")] });
    const { env, calls } = makeEnv({
        "/api/recommendations/context": both, "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": both,
        "/api/recommendations/context?namespace=bookinfo": both,
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": detailBody(),
        "/api/recommendations/declared/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": declaredBody(),
    });
    const c = C.createController(env);

    const frozenView = await c.start();
    assert.equal(frozenView.workflowKind, "frozen", "the only frozen workflow is chosen for you, not the declared one");
    assert.deepEqual(plain(frozenView.workflows.map((w) => w.value)), ["bookinfo-productpage", "declared:bookinfo-productpage"]);

    const declaredView = await c.selectWorkflow("declared:bookinfo-productpage");
    assert.equal(declaredView.workflowKind, "declared");
    assert.ok(calls.at(-1).startsWith("/api/recommendations/declared/detail"));

    const backToFrozen = await c.selectWorkflow("bookinfo-productpage");
    assert.equal(backToFrozen.workflowKind, "frozen");
    assert.ok(calls.at(-1).startsWith("/api/recommendations/live/detail"));
});

test("a declared choice that stops applying is dropped, not guessed at", async () => {
    let applies = true;
    const body = () => noFrozen({ declaredWorkflows: [declared("checkout", { applicable: applies, reason: applies ? null : "Services not found" })] });
    const { env, calls } = makeEnv({
        "/api/recommendations/context": body, "/api/recommendations/context?namespace=bookinfo": body, [DECLARED_DETAIL]: declaredBody(),
    });
    const c = C.createController(env);
    await c.start();
    await c.selectWorkflow("declared:checkout");
    const before = calls.length;

    applies = false;
    const view = await c.reload();

    assert.equal(view.workflowKind, "frozen");
    assert.equal(view.workflowId, "");
    assert.equal(view.status, "blocked");
    assert.ok(!calls.slice(before).some((u) => u.includes("declared/detail")), "nothing was requested for a workflow that no longer applies");
});

test("when the connected cluster changes a declared choice is cleared with the rest", async () => {
    const { env, calls, state } = makeEnv({
        "/api/recommendations/context": noFrozen(), "/api/recommendations/context?namespace=bookinfo": noFrozen(), [DECLARED_DETAIL]: declaredBody(),
    });
    const c = C.createController(env);
    await c.start();
    await c.selectWorkflow("declared:checkout");
    const before = calls.length;

    state.identity = "another-cluster";
    const view = await c.clusterChanged();

    assert.equal(view.workflowKind, "frozen");
    assert.equal(c.selection().workflowId, "");
    assert.ok(!calls.slice(before).some((u) => u.includes("declared/detail")));
});

test("a context without any declared workflows behaves exactly as before", async () => {
    const { env } = makeEnv({
        "/api/recommendations/context": context(),
        "/api/recommendations/context?namespace=bookinfo&workflow_id=bookinfo-productpage": context(),
        "/api/recommendations/live/detail?workflow_id=bookinfo-productpage&namespace=bookinfo": detailBody(),
    });

    const view = await C.createController(env).start();

    assert.equal(view.status, "ready");
    assert.equal(view.workflowKind, "frozen");
    assert.deepEqual(plain(view.workflows.map((w) => w.declared)), [false]);
});
