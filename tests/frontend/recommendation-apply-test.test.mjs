import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-apply-test.js", import.meta.url), "utf8");
function node(tag) { return { tag, children: [], attributes: {}, listeners: {}, textContent: "",
    setAttribute(k,v) { this.attributes[k] = v; }, append(...n) { this.children.push(...n); },
    replaceChildren(...n) { this.children = n; }, addEventListener(k,v) { this.listeners[k] = v; } }; }
function walk(root) { return [root, ...root.children.flatMap(walk)]; }
const text = (root) => walk(root).map((n) => n.textContent).join(" ");
function setup(handler, extra = {}) {
    const window = {}, calls = [], root = node("section");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const panel = window.ResilienceApplyTest.createPanel({ root, document: { createElement: node },
        fetchImpl: async (url, init = {}) => { calls.push([url, init.method || "GET", init.body]); return handler(url, init); }, ...extra });
    return { panel, root, calls };
}
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
const running = { runId: "run-1", requestKey: "click-1", status: "RUNNING", candidateId: "pair", namespace: "shop", declared: true,
    current: { command: "GET Deployment rollout status", message: "Waiting for replicas" }, events: [], report: { faultInjected: false } };
const complete = { ...running, status: "COMPLETED_WITH_GAPS", report: { faultInjected: false, riskMeaning: "Model recalculation",
    before: { workflows: { rows: [{ workflowId: "checkout", readiness: "READY", httpStatus: "NOT_CONFIGURED", reason: "No HTTP check" }] } },
    after: { workflows: { rows: [{ workflowId: "checkout", readiness: "READY", httpStatus: "NOT_CONFIGURED", reason: "No HTTP check" }] } } } };

test("opening and restoring a run only sends GET, and displays actual progress", async () => {
    const env = setup(() => response({ run: running }));
    await env.panel.start();
    assert.ok(text(env.root).includes("GET Deployment rollout status"));
    assert.ok(text(env.root).includes("Waiting for replicas"));
    assert.ok(env.calls.every(([, method]) => method === "GET"));
});

test("one Apply click sends one POST and polling never repeats it", async () => {
    const env = setup((_url, init) => response(init.method === "POST" ? running : complete));
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    await env.panel.start();
    assert.equal(env.calls.filter(([, m]) => m === "POST").length, 1);
    assert.equal(env.panel.state().run.status, "COMPLETED_WITH_GAPS");
    assert.ok(text(env.root).includes("NOT_CONFIGURED"));
    assert.ok(text(env.root).includes("unavailable"));
});

test("an uncertain POST cannot be presented as a successful older report", async () => {
    const env = setup((_url, init) => {
        if (init.method === "POST") throw new Error("connection lost");
        return response({ run: { ...complete, requestKey: "older-click" } });
    });
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    await env.panel.start();
    assert.equal(env.panel.state().run, null);
    assert.ok(text(env.root).includes("connection lost"), "retain the actual start failure while polling an older report");
    assert.equal(env.calls.filter(([, m]) => m === "POST").length, 1);
});

test("a refused Apply remains visible after a successful status read with no run", async () => {
    const env = setup((_url, init) => init.method === "POST"
        ? response({ detail: { message: "Cluster writes are disabled" } }, 403) : response({ run: null }));
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    await env.panel.start();
    assert.match(text(env.root), /Cluster writes are disabled/);
    assert.equal(env.root.hidden, false);
    assert.equal(env.calls.filter(([, method]) => method === "POST").length, 1);
});

test("a host scrolling error cannot prevent the explicit Apply request", async () => {
    const env = setup(() => response(running));
    env.root.scrollIntoView = () => { throw new Error("host scroll unavailable"); };
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    assert.equal(env.calls.filter(([, method]) => method === "POST").length, 1);
    assert.match(text(env.root), /Current operation/);
});

test("an unreadable rejection keeps its HTTP status after polling", async () => {
    const env = setup((_url, init) => init.method === "POST"
        ? { ok: false, status: 502, json: async () => { throw new Error("not JSON"); } } : response({ run: null }));
    await env.panel.apply({ candidateId: "pair", requestKey: "click-1" });
    await env.panel.start();
    assert.match(text(env.root), /unreadable response \(HTTP 502\)/);
});

test("missing measurements and hostile strings remain plain text in the report", async () => {
    const env = setup(() => response({ run: { ...complete, current: { command: "<img src=x onerror=alert(1)>", message: "<script>bad</script>" } } }));
    await env.panel.start();
    assert.ok(text(env.root).includes("<script>bad</script>"));
    assert.ok(walk(env.root).every((n) => n.innerHTML === undefined));
    assert.ok(text(env.root).includes("No node failure was injected"));
});

test("an interrupted baseline is a partial report and measured units are readable", async () => {
    const env = setup(() => response({ run: { ...running, status: "INTERRUPTED", report: {
        before: { resources: { available: true, source: "kubelet-summary", nodes: [
            { name: "worker", usage: { cpu: "219654614n", memory: 811532288 } },
            { name: "other", usage: { cpu: "0n", memory: null } }], pods: [] } }
    } } }));
    await env.panel.start();
    const shown = text(env.root);
    assert.match(shown, /Partial report/);
    assert.match(shown, /Not collected: the run stopped/);
    assert.match(shown, /219\.655 mCPU/);
    assert.match(shown, /773\.94 MiB/);
    assert.match(shown, /0 mCPU/);
    assert.match(shown, /Unavailable/);
});

test("download reads the persisted report without starting any test", async () => {
    const downloads = [];
    const env = setup((url) => response(url.endsWith("/report") ? complete : { run: complete }), { download: (...args) => downloads.push(args) });
    await env.panel.start();
    const button = walk(env.root).find((n) => n.textContent === "Download full report (JSON)");
    await button.listeners.click();
    assert.equal(downloads[0][0].runId, "run-1");
    assert.ok(env.calls.every(([, m]) => m === "GET"));
});

test("Reset restores once, then returns candidate selection without applying anything", async () => {
    let restored = false, ready = 0;
    const env = setup((url, init) => {
        if (init.method === "POST") { assert.match(url, /\/reset$/); return response({}); }
        if (url.endsWith("/application")) restored = true;
        return response({ run: { ...complete, canReset: !restored, application: { status: restored ? "RESET" : "APPLIED" } } });
    }, { onReadyForNext: () => ready++ });
    await env.panel.start();
    await walk(env.root).find((n) => n.textContent === "Reset changes").listeners.click();
    assert.equal(ready, 1);
    assert.equal(env.panel.state().busy, false);
    assert.equal(env.calls.filter(([, method]) => method === "POST").length, 1);
    assert.ok(env.calls.every(([url, method]) => method !== "POST" || url.endsWith("/reset")));
});

test("saved reports and comparisons remain read-only while the current candidate is separate", async () => {
    const window = {}, calls = [], root = node("section");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const history = window.ResilienceApplyTest.createHistoryPanel({ root, document: { createElement: node },
        fetchImpl: async (url, init = {}) => {
            calls.push([url, init.method || "GET"]);
            return response(url.includes("/history?") ? { runs: [{ runId: "saved-1", candidateId: "old-candidate", createdAt: "2026-10-24", status: "INTERRUPTED", baselineRisk: 100, predictedRisk: 0 }], hasMore: false }
                : { ...complete, runId: "saved-1", candidateId: "old-candidate", canReset: true });
        } });
    await history.start();
    const check = walk(root).find((n) => n.type === "checkbox");
    check.checked = true; check.listeners.change();
    assert.match(text(root), /Selected test comparison/);
    assert.match(text(root), /Before.*100%.*Predicted.*0%/, "the risk row draws before and predicted");
    assert.ok(walk(root).some((n) => n.attributes["aria-label"] === "Unavailable"), "the missing measured risk is a labelled dash, not a number");
    await walk(root).find((n) => n.textContent === "View saved report").listeners.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.ok(calls.some(([url]) => url.endsWith("/saved-1")));
    assert.ok(!walk(root).some((n) => n.textContent === "Reset changes"));
    assert.ok(calls.every(([, method]) => method === "GET"));
    history.destroy();
});

test("candidate sidebar shows the latest saved result and distinguishes an earlier proposal from an untested candidate", () => {
    const window = {}, root = node("aside"), selected = [], opened = [];
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node },
        strategyName: (type) => type, onSelect: (id) => selected.push(id), onReport: (id) => opened.push(id) });
    sidebar.update({ namespace: "shop", selectedId: "tested", historyLoaded: true,
        candidates: [{ id: "new", type: "REPLICA_INCREASE", service: "db", approval: { ready: true } },
            { id: "tested", type: "COMBINATION", service: "db+cache", approval: { ready: true }, yamlProposal: { sha256: "new-yaml" } }],
        history: [{ candidateId: "tested", namespace: "shop", runId: "latest", status: "INTERRUPTED", yamlSha256: "old-yaml" },
            { candidateId: "tested", namespace: "shop", runId: "older", status: "COMPLETED" }] });
    assert.match(text(root), /Not tested/);
    assert.match(text(root), /Interrupted/);
    assert.match(text(root), /Earlier proposal/);
    assert.match(text(walk(root).find((n) => n.tag === "article")), /Interrupted/, "saved results appear before untested choices");
    assert.ok(!text(root).includes("Checks passed"), "an older success must not replace the latest attempt");
    walk(root).find((n) => n.textContent === "Select candidate").listeners.click();
    walk(root).find((n) => n.textContent === "View saved report").listeners.click();
    assert.deepEqual(selected, ["new"]);
    assert.deepEqual(opened, ["latest"]);
});

test("unread or partially loaded history never marks candidates as definitely untested", () => {
    const window = {}, root = node("aside");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node }, strategyName: (type) => type });
    const state = { candidates: [{ id: "new", type: "SPREAD" }], history: [] };
    sidebar.update({ ...state, historyLoaded: false });
    assert.match(text(root), /Reading results/);
    sidebar.update({ ...state, historyLoaded: true, hasMore: true });
    assert.match(text(root), /Older results not loaded/);
    assert.ok(!text(root).includes("Not tested"));
    sidebar.update({ ...state, historyLoaded: false, error: "offline" });
    assert.match(text(root), /Results unavailable/);
});

test("completed functional checks cannot hide a missed resilience target", async () => {
    const env = setup(() => response({ run: { ...complete, status: "COMPLETED", report: { ...complete.report,
        riskTargetMet: false, predictedRisk: 0, riskAnalysis: { observed: { blastRadius: 100 } } } } }));
    await env.panel.start();
    assert.match(text(env.root), /Risk target not met/);
    assert.match(text(env.root), /expected protection was not achieved/);
    assert.match(text(env.root), /after-rollout model risk 100%/);
});

test("sidebar risk mismatch takes precedence over a completed run label", () => {
    const window = {}, root = node("aside");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node }, strategyName: (type) => type });
    sidebar.update({ historyLoaded: true, history: [{ candidateId: "missed", runId: "test-1", status: "COMPLETED", riskTargetMet: false }] });
    assert.match(text(root), /Risk target not met/);
    assert.ok(!text(root).includes("Checks passed"));
});

// ---- Active is only a change that was applied; Selected is only what is being looked at -------------------------------------------

test("a run is active only while its change is on the cluster: running, or finished and not yet reset", async () => {
    const check = async (run) => { const env = setup(() => response({ run })); await env.panel.start(); return env.panel.state().active; };
    assert.equal(await check(running), true, "running");
    assert.equal(await check({ ...complete, canReset: true }), true, "finished, not reset");
    assert.equal(await check({ ...complete, application: { status: "RESET" } }), false, "restored");
    assert.equal(await check({ ...complete, status: "FAILED" }), false, "stopped before anything stayed applied");
    assert.equal(setup(() => response({ run: null })).panel.state().active, false, "nothing started");
});

test("the sidebar pins the applied candidate on top as Active, with its run data, and selecting another does not move it", () => {
    const window = {}, root = node("aside");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node }, strategyName: (type) => type, onSelect() {}, onReport() {} });
    const candidates = [
        { id: "other", type: "REPLICA_INCREASE", service: "db", approval: { ready: true }, predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0 }, cost: { resource: "LOW" } },
        { id: "applied", type: "COMBINATION", service: "db+cache", approval: { ready: true }, predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0 } },
    ];
    const activeRun = { ...complete, candidateId: "applied", report: { ...complete.report, baselineRisk: { blastRadius: 100 }, predictedRisk: 0, riskAnalysis: { observed: { blastRadius: 50 } } } };

    sidebar.update({ namespace: "shop", selectedId: "other", activeId: "applied", activeRun, candidates, history: [], historyLoaded: true });

    const cards = walk(root).filter((n) => n.tag === "article");
    assert.match(text(cards[0]), /COMBINATION/, "the applied change is first, although another candidate is selected");
    assert.equal(cards[0].attributes["data-active"], "true");
    assert.match(text(cards[0]), /Active/);
    assert.match(text(cards[0]), /100% \/ 0% \/ 50%/, "its run data: before / predicted / after rollout");
    assert.equal(cards[1].attributes["data-active"], "false");
    assert.ok(!/Active/.test(text(cards[1])), "a candidate that was only selected is never called Active");
    assert.match(text(cards[1]), /Selected/);
    assert.match(text(cards[1]), /100% → 0%/, "the selected candidate shows its predicted data");
    assert.match(text(cards[1]), /not a measurement/);
});

test("with nothing applied, no candidate is called Active; the selected one shows its data", () => {
    const window = {}, root = node("aside");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node }, strategyName: (type) => type, onSelect() {} });
    sidebar.update({ namespace: "shop", selectedId: "a", candidates: [{ id: "a", type: "SPREAD", service: "web", approval: { ready: true },
        predicted: { blastRadiusBefore: 80, blastRadiusAfter: 20 } }], history: [], historyLoaded: true });
    const card = walk(root).find((n) => n.tag === "article");
    assert.ok(!/Active/.test(text(card)) && card.attributes["data-active"] === "false");
    assert.match(text(card), /Selected/);
    assert.match(text(card), /80% → 20%/);
});

test("while a run is busy the live view names each Deployment, pod and node, and it is dropped once the run ends", async () => {
    const live = { available: true, capturedAt: "2026-10-25T05:31:06Z", namespace: "shop", nodes: [{ name: "worker-a", pods: 1 }],
        deployments: [{ name: "db", desired: 2, ready: 1, updated: 1, pods: [
            { name: "db-0", node: "worker-a", state: "Ready", restarts: 0 }, { name: "db-1", node: null, state: "Unschedulable", restarts: 0 }] }] };
    let finished = false;
    const env = setup((url) => url.endsWith("/live") ? response(live) : response({ run: finished ? complete : running }));
    await env.panel.start();
    const shown = text(env.root);
    assert.ok(env.calls.some(([url]) => url.endsWith("/run-1/live")) && env.calls.every(([, method]) => method === "GET"));
    for (const expected of ["Live activity", "db — 1/2 ready", "db-0", "worker-a", "db-1", "Not scheduled yet", "Unschedulable", "Nodes", "Pods from this run"])
        assert.ok(shown.includes(expected), expected);
    finished = true;
    await env.panel.start();
    assert.ok(!text(env.root).includes("Live activity"));
});

test("a live view that cannot be read leaves the run's own status in place", async () => {
    const env = setup((url) => url.endsWith("/live") ? response({ detail: "gone" }, 500) : response({ run: running }));
    await env.panel.start();
    assert.ok(text(env.root).includes("Reading what the cluster is doing"));
    assert.ok(text(env.root).includes("Waiting for replicas"));
});

test("a refused Reset stays shown until the connected cluster is no longer the one it was refused on", async () => {
    let match = "same", refuse = true;
    const finished = { ...complete, canReset: true };
    const env = setup((url, init) => init.method === "POST" && refuse
        ? response({ detail: { message: "The connected cluster is not the one this change was applied to" } }, 409)
        : response({ run: { ...finished, clusterMatch: match } }), { readOnly: false });
    await env.panel.start();
    await env.panel.reset();
    assert.ok(text(env.root).includes("not the one this change was applied to"));
    await env.panel.start();                                  // a re-check on the same cluster keeps the explanation
    assert.ok(text(env.root).includes("not the one this change was applied to"));
    match = "other";                                          // another cluster is connected now: the old refusal is not about it
    await env.panel.start();
    assert.ok(!text(env.root).includes("not the one this change was applied to"));
});

test("the live view tells old pods from new ones and shows node health and usage", async () => {
    const live = { available: true, capturedAt: "2026-10-25T05:31:06Z", namespace: "shop", deployments: [{ name: "db", desired: 2, ready: 2, updated: 1,
        pods: [{ name: "db-new", node: "worker-a", state: "Ready", version: "new" }, { name: "db-old", node: "worker-b", state: "Terminating", version: "old" }] }],
        nodes: [{ name: "worker-a", pods: 1, ready: true, schedulable: true, cpu: "250m", memory: "512Mi" },
                { name: "worker-b", pods: 1, ready: true, schedulable: false, cpu: null, memory: null }] };
    const env = setup((url) => url.endsWith("/live") ? response(live) : response({ run: running }));
    await env.panel.start();
    const shown = text(env.root);
    for (const expected of ["New version", "Old version", "Cordoned: takes no new pods", "250 mCPU", "512 MiB", "Unavailable"])
        assert.ok(shown.includes(expected), expected);
});

test("the sidebar offers Select only where the page's own rule would accept the click, and says why not otherwise", () => {
    const window = {}, root = node("aside"), picked = [];
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    // A declared workflow: only a research approval counts. `approval.ready` alone is not enough.
    const canSelect = (c) => !!c.researchApproval?.available;
    const sidebar = window.ResilienceApplyTest.createCandidateResultsSidebar({ root, document: { createElement: node }, strategyName: (type) => type,
        onSelect: (id) => picked.push(id), canSelect, whyNotSelectable: (c) => c.researchApproval?.reason });
    sidebar.update({ namespace: "shop", selectedId: "a", history: [], historyLoaded: true, candidates: [
        { id: "a", type: "SPREAD", service: "web", approval: { ready: true }, researchApproval: { available: true } },
        { id: "b", type: "SPREAD", service: "db", approval: { ready: true }, researchApproval: { available: false, reason: "Not verified feasible for research approval." } }] });
    const [a, b] = walk(root).filter((n) => n.tag === "article");
    assert.equal(walk(a).filter((n) => n.tag === "button").length, 1);
    assert.equal(walk(b).filter((n) => n.tag === "button").length, 0, "no button that the click handler would silently ignore");
    assert.match(text(b), /Not verified feasible for research approval/);
});

// ---- the saved-test comparison: what it decides to show --------------------------------------------------------------------------------
function load() { const w = {}; vm.runInNewContext(source, { window: w, setTimeout: () => 1, clearTimeout() {} }); return w.ResilienceApplyTest.compare; }
const cmp = load();
const plain = (value) => JSON.parse(JSON.stringify(value));
const savedRun = (extra) => ({ runId: "apply-test-deb04f5ae5f044fcae5c099145cc76db", candidateId: "rec-all-workflows-productpage+ratings-combination",
    createdAt: "2026-10-25T05:31:06.731478", status: "COMPLETED_WITH_GAPS", clusterUid: "uid-a", workflowSetDigest: "digest-1",
    baselineRisk: 100, predictedRisk: 0, afterRisk: 0, ...extra });

test("identifiers are shortened for reading and times are said the way a person says them", () => {
    assert.equal(cmp.shortId("apply-test-deb04f5ae5f044fcae5c099145cc76db"), "deb04f5a…");
    assert.equal(cmp.shortId("abc"), "abc");
    assert.equal(cmp.shortId(null), null);
    const options = { now: "2026-10-25T09:00:00Z", timeZone: "UTC" };
    assert.equal(cmp.friendlyTime("2026-10-25T05:31:06.731478", options), "Today 05:31");
    assert.equal(cmp.friendlyTime("2026-10-24T11:40:14.059232", options), "Yesterday 11:40");
    assert.equal(cmp.friendlyTime("2026-10-20T11:40:14", options), "20 Oct 11:40");
    assert.equal(cmp.friendlyTime("not a time", options), "not a time", "an unreadable value is shown as it is, not invented");
});

test("a column is titled by strategy and services, also for an older run that stored only a candidate id", () => {
    const named = cmp.candidateTitle({ candidateId: "x", candidate: { type: "TOPOLOGY_SPREAD", service: "web" } });
    assert.deepEqual(plain(named), { strategy: "Topology spread", services: "web", id: "x" });
    const old = cmp.candidateTitle({ candidateId: "rec-all-workflows-details+productpage-combination" });
    assert.deepEqual(plain(old), { strategy: "Combination", services: "details + productpage", id: "rec-all-workflows-details+productpage-combination" });
    assert.equal(cmp.candidateTitle({ candidateId: "odd" }).services, "odd");
});

test("a run on another cluster is marked, and the rest are not", () => {
    const model = cmp.buildComparison([savedRun(), savedRun({ runId: "r2" }), savedRun({ runId: "r3", clusterUid: "uid-b" })]);
    assert.equal(model.clusterMismatch, true);
    assert.deepEqual(model.columns.map((c) => c.otherCluster), [false, false, true]);
    const cluster = model.sections.flatMap((s) => s.rows).find((r) => r.label === "Cluster");
    assert.deepEqual(cluster.cells.map((c) => !!c.differs), [false, false, true]);
    assert.equal(cmp.buildComparison([savedRun(), savedRun({ runId: "r2" })]).clusterMismatch, false);
});

test("the risk row flags a measured risk that differs from the predicted one, and a missing value stays missing", () => {
    const rows = cmp.buildComparison([savedRun({ afterRisk: 100 }), savedRun({ runId: "r2", afterRisk: 0 }), savedRun({ runId: "r3", afterRisk: undefined })])
        .sections.flatMap((s) => s.rows).find((r) => r.label === "Before / predicted / after").cells;
    assert.deepEqual(rows.map((c) => c.mismatch), [true, false, false]);
    assert.equal(rows[2].after, null, "unknown is not 0%");
});

test("resource cells show before, after and the change without calling a rise good or bad", () => {
    const cell = plain(cmp.usageCell(5.221, 2236.479, "mCPU"));
    assert.deepEqual([cell.before, cell.after, cell.delta, cell.unit], ["5.2", "2,236", "+42,736%", "mCPU"]);
    const missing = plain(cmp.usageCell(6.07, null, "mCPU"));
    assert.deepEqual([missing.before, missing.after, missing.delta], ["6.1", null, null]);
});

test("rows that read the same in every run are marked, and the toggle hides only those", async () => {
    const window = {}, root = node("section");
    vm.runInNewContext(source, { window, setTimeout: () => 1, clearTimeout() {} });
    const rows = [savedRun(), savedRun({ runId: "apply-test-second", candidateId: "rec-all-workflows-details+reviews-combination", afterRisk: 100 })];
    const history = window.ResilienceApplyTest.createHistoryPanel({ root, document: { createElement: node }, selectedId: () => rows[0].candidateId,
        fetchImpl: async () => response({ runs: rows, hasMore: false }) });
    await history.start();
    walk(root).filter((n) => n.type === "checkbox").forEach((c) => { c.checked = true; c.listeners.change(); });
    assert.ok(text(root).includes("Cluster") && text(root).includes("Workflow version"));
    assert.ok(text(root).includes("Productpage") || text(root).includes("productpage + ratings"));
    const toggle = walk(root).find((n) => n.type === "checkbox" && !n.attributes["aria-label"]);
    toggle.checked = true; toggle.listeners.change();
    const labels = walk(root).filter((n) => n.className === "cmp-label").map((n) => n.textContent);
    assert.ok(!labels.includes("Cluster") && !labels.includes("Workflow version"), "identical rows are hidden");
    assert.ok(labels.includes("Before / predicted / after") && labels.includes("Run"), "rows that differ, or identify a run, stay");
    assert.ok(walk(root).some((n) => n.attributes["data-selected"] === "true"), "the selected candidate's column is marked");
});
