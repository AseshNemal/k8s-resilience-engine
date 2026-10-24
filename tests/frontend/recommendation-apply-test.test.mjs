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
    assert.match(text(root), /100% \/ 0% \/ Unavailable/);
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
