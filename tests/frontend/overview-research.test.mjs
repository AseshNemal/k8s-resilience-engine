// The Overview's research summary: the pure descriptions (overview-summary.js) and the page script (dashboard.js) drawing them against a
// minimal fake DOM. Every number is a body handed in by the test; nothing here comes from a cluster.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const sources = ["connection-state.js", "recommendation-apply-test.js", "overview-summary.js"].map(read);
const dashboard = read("components/dashboard.js");

class FakeNode {
    constructor(tag) { this.style = {}; this.tagName = tag; this.children = []; this.attributes = {}; this.className = ""; this.textContent = ""; this.hidden = false; this.disabled = false; this.listeners = {}; }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute(name, value) { this.attributes[name] = String(value); }
    getAttribute(name) { return this.attributes[name] ?? null; }
    removeAttribute(name) { delete this.attributes[name]; }
    querySelectorAll(selector) {
        const matches = (node, part) => {
            if (part.startsWith(".")) return node.className.split(" ").includes(part.slice(1));
            const attr = part.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);
            return attr ? node.getAttribute(attr[1]) !== null && (attr[2] === undefined || node.getAttribute(attr[1]) === attr[2]) : false;
        };
        const result = [];
        const visit = (node) => { for (const child of node.children) { if (selector.split(",").some((part) => matches(child, part.trim()))) result.push(child); visit(child); } };
        visit(this); return result;
    }
    addEventListener(type, handler) { this.listeners[type] = handler; }
    text() { return [this.textContent, ...this.children.map((child) => child.text())].join(" "); }
}

const ROLES = ["overview-error-region", "reload-button", "source-badge", "discovery-summary", "overview-namespaces", "last-refreshed", "discovery-headline",
    "discovery-notes", "discovery-provenance", "overview-applied", "overview-applied-text", "overview-status", "overview-risk", "overview-risk-headline",
    "overview-risk-sub", "overview-results", "overview-placement", "overview-evidence", "overview-scope", "overview-next-text", "overview-next-link", "overview-node-impact", "overview-workflow-impact", "overview-workflow-caption", "overview-fix", "overview-latest-test",
    "overview-focus-summary", "overview-focus-clear", "overview-focus-view", "overview-placement-focus",
    ...["cluster", "workflow", "prediction", "test"].flatMap((id) => [`overview-chip-${id}`, `overview-chip-${id}-state`, `overview-chip-${id}-detail`])];

function boot(respond, { withModules = true } = {}) {
    const nodes = Object.fromEntries(ROLES.map((role) => [`[data-role="${role}"]`, new FakeNode("div")]));
    const calls = [];
    let onLoad;
    const document = {
        createElement: (tag) => new FakeNode(tag),
        querySelector: (selector) => nodes[selector] || new FakeNode("div"),
        addEventListener: (type, handler) => { if (type === "DOMContentLoaded") onLoad = handler; },
    };
    const window = {
        document,
        ResilienceApi: { fetchJson: async (url) => { calls.push(url); return respond(url); } },
        ResilienceUi: { updateLastRefreshed: () => {} },
    };
    const context = vm.createContext({ window, document, console, globalThis: window });
    vm.runInContext(sources[0], context);
    if (withModules) { vm.runInContext(sources[1], context); vm.runInContext(sources[2], context); vm.runInContext(read("recommendation-topology.js"), context); }
    vm.runInContext(dashboard, context);
    return { nodes, calls, window, load: async () => { await onLoad(); await new Promise((resolve) => setTimeout(resolve, 20)); } };
}

const node = (page, role) => page.nodes[`[data-role="${role}"]`];
const plain = (value) => JSON.parse(JSON.stringify(value));

const discovery = { status: "ok", cluster: { connectionFingerprint: "abc", identityVerified: false }, capturedAt: "2026-10-24T10:00:00Z", excludedNamespaces: [],
    totals: { workerNodes: 2, services: 4, unresolved: 0 }, namespaces: [{ namespace: "bookinfo", status: "ok", unresolved: [], graph: { nodes: [], services: [] } }] };
const context = { namespace: "bookinfo", workflows: [], declaredWorkflows: [{ workflowId: "productpage-view", applicable: true }, { workflowId: "x", applicable: true }],
    evidence: [{ id: "cluster", label: "Connected cluster", state: "available", detail: "ok" }, { id: "workflow", label: "Approved frozen workflow", state: "missing", detail: "None frozen." }], nextActions: [] };
const detail = { evidence: { failedNode: "desktop-worker2" }, baselineImpact: { blastRadius: 100, failedServices: ["productpage"], failedWorkflows: ["a", "b", "c"] },
    portfolio: { workflows: [{ workflowId: "a" }, { workflowId: "b" }, { workflowId: "c" }] }, currentDeployment: { services: [], nodes: [] }, candidates: [] };
const run = (over = {}) => ({ runId: "r1", candidateId: "c1", namespace: "bookinfo", status: "COMPLETED_WITH_GAPS", createdAt: "2026-10-24T11:00:00Z",
    candidate: { type: "COMBINATION", service: "ratings+reviews" }, baselineRisk: 100, predictedRisk: 100, afterRisk: 100,
    afterWorkflows: { rows: [{ workflowId: "a", httpStatus: "NOT_CONFIGURED" }, { workflowId: "b", httpStatus: "NOT_CONFIGURED" }] }, afterResources: true, ...over });

// ---- the pure descriptions ------------------------------------------------------------------------------------------------------

function loadSummary() {
    const window = {};
    vm.runInNewContext(sources[1], { window });
    vm.runInNewContext(sources[2], { window, globalThis: window });
    return window.ResilienceOverview;
}
const OV = loadSummary();

test("the current risk names the node and how many workflows are lost, and says it is predicted", () => {
    const risk = plain(OV.describeRisk(detail));
    assert.equal(risk.state, "available");
    assert.equal(risk.headline, "If desktop-worker2 fails, 3 of 3 workflows are lost");
    assert.match(risk.sub, /Blast radius 100%.*productpage.*not measured/);
});

test("a failure the workflows survive says so, and an answer without a baseline claims nothing", () => {
    const survives = { ...detail, baselineImpact: { blastRadius: 0, failedServices: [], failedWorkflows: [] } };
    assert.equal(plain(OV.describeRisk(survives)).headline, "If desktop-worker2 fails, every workflow survives");
    const none = plain(OV.describeRisk({ ...detail, baselineImpact: null }));
    assert.equal(none.state, "missing");
    assert.ok(!/\d+%/.test(none.headline + none.sub), "no figure is invented");
});

test("with no detail the risk is missing with the reason, or pending while it is computed", () => {
    assert.deepEqual(plain(OV.describeRisk(null, { message: "No workflow applies." })).sub, "No workflow applies.");
    assert.equal(OV.describeRisk(null, { message: "x" }).state, "missing");
    assert.equal(OV.describeRisk(null, { pending: true }).state, "pending");
});

test("the status strip: each chip is available, missing or pending from what is known so far, and links to the page that moves it on", () => {
    const chips = plain(OV.describeChips({ discovery: true, context, risk: OV.describeRisk(detail), history: { runs: [run()] } }));
    assert.deepEqual(chips.map((c) => [c.id, c.state]), [["cluster", "available"], ["workflow", "available"], ["prediction", "available"], ["test", "incomplete"]]);
    assert.match(chips[1].detail, /2 declared workflows apply \(declared, not observed\)/);
    assert.match(chips[3].detail, /1 saved test; latest: missing checks/);
    assert.deepEqual(chips.map((c) => c.href), ["/components/deployments", "/components/workflows", "/components/failure-impacts", "/components/recommendations"]);

    const early = plain(OV.describeChips({ discovery: true }));
    assert.deepEqual(early.map((c) => c.state), ["available", "pending", "pending", "pending"]);
    const bare = plain(OV.describeChips({ discovery: false, context: { workflows: [], declaredWorkflows: [], evidence: [{ id: "workflow", detail: "None frozen." }] }, history: { runs: [] } }));
    assert.deepEqual(bare.map((c) => c.state), ["missing", "missing", "pending", "missing"]);
    assert.equal(plain(OV.describeChips({ discovery: true, history: null }))[3].state, "missing", "a history that could not be read is not 'checking'");
});

test("the applied-change banner shows only while a change is on the cluster", () => {
    const body = (status) => ({ application: { status, namespace: "bookinfo", deployments: ["productpage-v1"], appliedAt: "2026-10-24T11:00:00" } });
    assert.equal(OV.describeApplied(body("APPLIED")).visible, true);
    assert.match(OV.describeApplied(body("APPLIED")).text, /bookinfo · productpage-v1 · applied/);
    assert.equal(OV.describeApplied(body("RESET_FAILED")).visible, true);
    for (const closed of [body("RESET"), body("FAILED"), { application: null }, null]) assert.equal(OV.describeApplied(closed).visible, false);
});

test("the results table keeps the latest saved test per candidate, newest first, with predicted and measured apart", () => {
    const history = { runs: [run({ runId: "new", afterRisk: 0, predictedRisk: 0, status: "COMPLETED" }), run({ runId: "old" }), run({ runId: "other", candidateId: "c2", candidate: { type: "REPLICA_INCREASE", service: "productpage" }, afterRisk: null, riskTargetMet: false })] };
    const rows = plain(OV.describeResults(history));
    assert.deepEqual(rows.map((r) => r.runId), ["new", "other"], "the older run of the same candidate is not listed");
    assert.equal(rows[0].name, "Combination on ratings+reviews");
    assert.deepEqual([rows[0].baseline, rows[0].predicted, rows[0].after, rows[0].outcome], ["100%", "0%", "0%", "Checks passed"]);
    assert.deepEqual([rows[1].after, rows[1].outcome], ["unavailable", "Risk target not met"], "a missing measurement is not a number");
    assert.equal(OV.describeResults(null).length, 0);
});

test("the evidence panel adds what the latest test says about workflow checks and metrics, and is honest before any test", () => {
    const withTest = plain(OV.describeEvidence(context, { runs: [run()] }));
    const checks = withTest.find((item) => item.id === "workflowChecks");
    assert.deepEqual([checks.state, checks.detail.startsWith("0 of 2 workflows")], ["missing", true]);
    assert.equal(withTest.find((item) => item.id === "metrics").state, "available");
    const partial = plain(OV.describeEvidence(context, { runs: [run({ afterWorkflows: { rows: [{ workflowId: "a", httpStatus: "PASSED" }, { workflowId: "b", httpStatus: "NOT_CONFIGURED" }] } })] }));
    assert.equal(partial.find((item) => item.id === "workflowChecks").state, "incomplete");
    const none = plain(OV.describeEvidence(context, { runs: [] }));
    assert.deepEqual(none.slice(-2).map((i) => i.state), ["pending", "pending"]);
    assert.equal(none[0].label, "Connected cluster", "the backend's own checklist comes first");
});

// ---- the page --------------------------------------------------------------------------------------------------------------------

function respond({ history = { runs: [run()], hasMore: false }, applied = { application: { status: "APPLIED", namespace: "bookinfo", deployments: ["ratings-v1"] } } } = {}) {
    return (url) => {
        if (url === "/api/deployments/discovery/all") return discovery;
        if (url === "/api/recommendations/context") return context;
        if (url.startsWith("/api/recommendations/apply-tests/history")) { if (history instanceof Error) throw history; return history; }
        if (url === "/api/recommendations/applications/current") return applied;
        if (url.startsWith("/api/recommendations/declared/detail")) return detail;
        throw new Error(`unexpected request ${url}`);
    };
}

test("the page draws the strip, the risk, the banner, the results and the evidence from what the backend returned, using reads only", async () => {
    const page = boot(respond());
    await page.load();

    assert.deepEqual(plain(page.calls).sort(), ["/api/deployments/discovery/all", "/api/recommendations/apply-tests/history?limit=50", "/api/recommendations/applications/current",
        "/api/recommendations/context", "/api/recommendations/declared/detail?namespace=bookinfo&all_workflows=true"].sort());
    assert.match(node(page, "overview-risk-headline").textContent, /If desktop-worker2 fails, 3 of 3 workflows are lost/);
    assert.equal(node(page, "overview-risk").attributes["data-lost"], "true");
    assert.equal(node(page, "overview-applied").hidden, false);
    assert.match(node(page, "overview-applied-text").textContent, /ratings-v1/);
    assert.deepEqual(["cluster", "workflow", "prediction", "test"].map((id) => node(page, `overview-chip-${id}`).attributes["data-state"]), ["available", "available", "available", "incomplete"]);
    assert.match(node(page, "overview-chip-workflow-detail").textContent, /2 declared workflows apply/);
    assert.equal(node(page, "overview-chip-test-state").textContent, "Missing checks");
    assert.match(node(page, "overview-results").text(), /Combination on ratings\+reviews[\s\S]*Missing checks/);
    assert.match(node(page, "overview-evidence").text(), /Workflow checks[\s\S]*0 of 2 workflows have an HTTP check configured/);
});

test("when saved tests cannot be read the page says so; it does not draw an empty success", async () => {
    const page = boot(respond({ history: new Error("history unavailable") }));
    await page.load();

    assert.equal(node(page, "overview-chip-test").attributes["data-state"], "missing");
    assert.match(node(page, "overview-chip-test-detail").textContent, /Saved tests could not be read/);
    assert.match(node(page, "overview-results").text(), /Saved tests could not be read/);
    assert.match(node(page, "overview-risk-headline").textContent, /desktop-worker2/, "the other parts are unaffected");
});

test("with no workflow that applies the page asks for no detail and shows why", async () => {
    const page = boot((url) => (url === "/api/recommendations/context" ? { ...context, declaredWorkflows: [], nextActions: ["Declare a workflow whose services are in this application."] } : respond()(url)));
    await page.load();

    assert.ok(!page.calls.some((url) => url.includes("/detail")), "no recommendation is computed for nothing to compute it from");
    assert.match(node(page, "overview-risk-sub").textContent, /Declare a workflow/);
    assert.match(node(page, "overview-placement").text(), /Declare a workflow/);
});

test("without the summary module the page still draws the cluster and asks for nothing else", async () => {
    const page = boot(respond(), { withModules: false });
    await page.load();

    assert.deepEqual(plain(page.calls), ["/api/deployments/discovery/all"]);
});


test("failed and interrupted runs never receive a successful test state", () => {
    for (const [status, expected, label] of [["FAILED", "missing", "Run stopped"], ["INTERRUPTED", "incomplete", "Interrupted"], ["COMPLETED", "available", "Checks passed"]]) {
        const chip = OV.describeChips({ history: { runs: [run({ status })] } })[3];
        assert.equal(chip.state, expected); assert.equal(chip.statusLabel, label);
    }
});

test("node percentages and workflow causes come from explicit predictor evidence", () => {
    const model = plain(OV.describeImpact({ ...detail,
        nodeRiskRanking: [{ node: "w1", blastRadius: 75 }, { node: "w2", blastRadius: 25 }, { node: "invalid", blastRadius: null }],
        workflowExposure: { before: { a: ["desktop-worker2"], b: [] } },
        workflowDefinitions: [{ workflow_id: "a", mandatory_services: ["productpage"] }],
    }));
    assert.deepEqual(model.nodes, [{ node: "w1", value: 75 }, { node: "w2", value: 25 }]);
    assert.deepEqual(model.workflows.map((w) => w.failed), [true, false, null]);
    assert.deepEqual(model.workflows[0].causes, ["productpage"]);
    assert.equal(OV.describeImpact(null).nodes.length, 0);
});

test("placement distinguishes concentrated, unready, unresolved and unknown pods", () => {
    const matrix = plain(OV.describeMatrix({ namespaces: [{ namespace: "shop", graph: { nodes: [{ name: "w1" }, { name: "w2" }], services: [
        { name: "db", pods: [{ node: "w1", ready: true }, { node: "w1", ready: true }, { node: "w2", ready: false }, { node: "unknown", ready: false }] },
        { name: "missing", pods: [{ node: "w1" }] },
    ] } }] }));
    assert.deepEqual(matrix.rows[0].cells, [2, 0]);
    assert.equal(matrix.rows[0].concentrated, true);
    assert.equal(matrix.rows[0].unresolved, 1);
    assert.equal(matrix.rows[0].unready, 2);
    assert.equal(matrix.rows[1].cells[0], null);
    assert.equal(matrix.rows[1].unready, null);
});

test("a failed refresh clears earlier research results and applied banner", async () => {
    let fail = false;
    const page = boot((url) => { if (fail) throw new Error("Disconnected"); return respond()(url); });
    await page.load();
    fail = true;
    await page.load();
    assert.equal(node(page, "overview-applied").hidden, true);
    assert.match(node(page, "overview-risk-sub").textContent, /Disconnected/);
    assert.doesNotMatch(node(page, "overview-results").text(), /Combination/);
    assert.match(node(page, "overview-latest-test").text(), /unavailable/);
});

test("a change on another cluster offers no applied-change banner here", () => {
    assert.equal(OV.describeApplied({ clusterMatch: "other", application: { status: "APPLIED" } }).visible, false);
});

const interactiveDetail = {
    evidence: { namespace: "bookinfo", failedNode: "w1" },
    portfolio: { workflows: [{ workflowId: "checkout" }] },
    baselineImpact: { blastRadius: 100, failedServices: ["db"], failedWorkflows: ["checkout"] },
    nodeRiskRanking: [{ node: "w1", blastRadius: 100 }, { node: "w2", blastRadius: 0 }],
    workflowExposure: { before: { checkout: ["w1"] } },
    workflowDefinitions: [{ workflow_id: "checkout", mandatory_services: ["db", "web"] }],
    currentDeployment: { nodes: [{ name: "w1" }, { name: "w2" }], services: [
        { name: "db", declaredReplicas: 1, readyReplicas: 1, concentrated: true, replicaConcentration: 1, pods: [{ name: "db-1", node: "w1", ready: true }] },
        { name: "web", declaredReplicas: 1, readyReplicas: 1, concentrated: true, replicaConcentration: 1, pods: [{ name: "web-1", node: "w2", ready: true }] },
    ] },
    candidates: [{ selected: true, type: "REPLICA_INCREASE", service: "db", predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0, riskReduction: 100 },
        cost: { resource: "MEDIUM", note: "Adds a replica." }, recommendedChange: "Add one replica and topology spread." }],
};

test("the next action names the proposed change, benefit and cost without guessing missing values", () => {
    const action = plain(OV.describeAction(interactiveDetail));
    assert.deepEqual(action.changes, [{ service: "db", change: "Add one replica and spread" }]);
    assert.equal(action.benefit, 100); assert.equal(action.cost, "MEDIUM");
    assert.match(OV.describeAction({ candidates: [{ ...interactiveDetail.candidates[0], type: "COMBINATION" }] }).costNote, /highest member.*not total resource usage/);
    assert.equal(OV.describeAction({ candidates: [{ selected: true, type: "X" }] }).benefit, null);
    assert.equal(OV.describeAction(null).reviewable, false);
});

test("service indicators keep readiness separate from concentration and retain unknown evidence", () => {
    const complete = OV.describeService(interactiveDetail.currentDeployment.services[0]);
    assert.equal(complete.readyShare, 1); assert.equal(complete.concentrated, true);
    assert.equal(OV.describeService({ readyReplicas: 1, declaredReplicas: 3 }).readiness, "incomplete");
    assert.equal(OV.describeService({ readyReplicas: 0, declaredReplicas: 0 }).readyShare, null);
    const unknown = OV.describeService({});
    assert.equal(unknown.readyShare, null); assert.equal(unknown.concentration, null); assert.equal(unknown.concentrated, null);
});

test("linked inspection distinguishes reported failure causes from observed dependencies at another node", () => {
    assert.deepEqual(plain(OV.describeFocus(interactiveDetail, "checkout", "w1")).services, ["db"]);
    const other = plain(OV.describeFocus(interactiveDetail, "checkout", "w2"));
    assert.deepEqual(other.services, ["web"]);
    assert.match(other.message, /Predicted to survive.*Dependencies observed here/);
    assert.doesNotMatch(other.message, /Services lost/);
    assert.equal(OV.describeFocus(interactiveDetail, "unknown", "w1"), null);
    assert.equal(OV.describeFocus(interactiveDetail, "checkout", "foreign-node"), null);
});

test("selecting a survival cell links to one topology and clears on explicit reset or refresh, using no new requests", async () => {
    const page = boot((url) => url.includes("/declared/detail") ? interactiveDetail : url === "/api/deployments/discovery/all"
        ? { ...discovery, namespaces: [{ namespace: "bookinfo", status: "ok", unresolved: [], graph: interactiveDetail.currentDeployment }] } : respond()(url));
    await page.load();
    const calls = page.calls.length;
    const cell = node(page, "overview-workflow-impact").querySelectorAll(".overview-survival-cell")[0];
    cell.listeners.click();
    assert.equal(cell.getAttribute("aria-pressed"), "true");
    assert.match(node(page, "overview-focus-summary").textContent, /Services lost: db/);
    assert.equal(node(page, "overview-focus-view").hidden, false);
    const db = node(page, "overview-placement").querySelectorAll('.rec-topo__service').find((el) => el.getAttribute("data-service") === "db");
    const web = node(page, "overview-placement").querySelectorAll('.rec-topo__service').find((el) => el.getAttribute("data-service") === "web");
    assert.equal(db.getAttribute("data-highlighted"), "true"); assert.equal(web.getAttribute("data-highlighted"), "false");
    assert.match(node(page, "overview-next-text").text(), /db.*Add one replica.*100 pp.*MEDIUM/);
    assert.equal(page.calls.length, calls, "inspection never recomputes or mutates cluster state");
    node(page, "overview-focus-clear").listeners.click();
    assert.equal(cell.getAttribute("aria-pressed"), "false");
    assert.equal(node(page, "overview-placement").getAttribute("data-focus-active"), "false");
    cell.listeners.click();
    await page.load();
    assert.equal(node(page, "overview-focus-clear").hidden, true, "a refresh cannot retain a previous cluster's highlight");
});
