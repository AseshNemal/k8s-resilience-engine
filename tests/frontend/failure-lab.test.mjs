// The Failure lab panel: nothing starts without an approved preview, a control plane is never offered, no name is asked, restore is always there.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document } from "./support/mini-dom.mjs";

const LAB = readFileSync(new URL("../../app/static/js/failure-lab.js", import.meta.url), "utf8");
const BASE = "/api/recommendations/validation-loop";

function setup(routes) {
    const doc = new Document();
    const root = doc.createElement("div"); doc.body.append(root);
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set, Promise };
    sandbox.window.document = undefined;
    vm.createContext(sandbox);
    vm.runInContext(LAB, sandbox, { filename: "failure-lab.js" });
    const calls = [];
    const fetchImpl = async (url, init = {}) => {
        const path = url.replace(BASE, ""); const method = init.method || "GET";
        calls.push([method, path, init.body ? JSON.parse(init.body) : null]);
        const hit = routes[`${method} ${path.split("?")[0]}`];
        if (!hit) return { ok: false, status: 404, json: async () => ({ detail: "not found" }) };
        const r = typeof hit === "function" ? hit(init) : hit;
        return { ok: r.ok !== false, status: r.status || 200, json: async () => r.body };
    };
    const timers = { set: () => 1, clear: () => {} };
    const module = sandbox.window.ResilienceFailureLab;
    const panel = module.createPanel({ root, document: doc, fetchImpl, namespace: () => "bookinfo", timers });
    return { panel, module, root, calls, doc };
}
const text = (n) => (n.textContent || "") + (n.children || []).map(text).join(" ");
const find = (n, pred, out = []) => { if (pred(n)) out.push(n); (n.children || []).forEach((c) => find(c, pred, out)); return out; };
const TARGETS = { targets: [{ kind: "DOCKER_DESKTOP", label: "Docker Desktop cluster", available: true, warning: "This is the cluster you use every day.", identity: { nodes: ["desktop-control-plane", "desktop-worker", "desktop-worker2"] } },
    { kind: "DISPOSABLE", label: "Disposable test cluster", available: false, reason: "not prepared", identity: null }] };
const PLAN = { canApprove: true, planDigest: "d1", acknowledgementsNeeded: ["kube-system/Deployment/coredns"], issues: [], notes: ["Failure lab: an experiment."],
    target: { kind: "DOCKER_DESKTOP" }, lab: { nodes: ["desktop-worker"], holdS: 60, allWorkersStopped: false, impact: { services: [{ service: "reviews", ready: 3, lost: 1, left: 2, state: "degraded" }, { service: "ratings", ready: 1, lost: 1, left: 0, state: "down" }] } } };

test("a control plane is never offered and nothing is sent until the person asks for a preview", async () => {
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run: null } } });
    await t.panel.load();
    const offered = find(t.root, (n) => n.tag === "input" && n.getAttribute("type") === "checkbox").map((n) => n.getAttribute("value"));
    assert.deepEqual(offered, ["desktop-worker", "desktop-worker2"]);
    assert.deepEqual(t.calls.map((c) => c[0]), ["GET", "GET"]);                        // only reads
    assert.deepEqual(t.module.chooseable(["a-control-plane", "w2", "w1"]), ["w1", "w2"]);
});

test("the preview needs at least one worker and a sensible hold, and sends exactly what was chosen", async () => {
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run: null } }, "POST /plan": { body: PLAN } });
    await t.panel.load();
    assert.equal(t.module.canPreview({ nodes: new Set(), hold: 60 }), false);
    assert.equal(t.module.canPreview({ nodes: new Set(["w"]), hold: 5 }), false);
    t.panel.form.nodes.add("desktop-worker"); t.panel.form.hold = 60;
    await t.panel.preview();
    const sent = t.calls.find((c) => c[1] === "/plan")[2];
    assert.equal(sent.mode, "LAB"); assert.deepEqual(sent.nodes, ["desktop-worker"]); assert.equal(sent.holdSeconds, 60); assert.equal(sent.probe, null);
    assert.match(text(t.root), /ratings/); assert.match(text(t.root), /goes offline/); assert.match(text(t.root), /keeps 2 of 3/);
});

test("starting needs every essential workload accepted by name and, on the everyday cluster, an explicit tick", async () => {
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run: null } }, "POST /plan": { body: PLAN } });
    await t.panel.load(); t.panel.form.nodes.add("desktop-worker"); await t.panel.preview();
    const ack = new Set(), none = new Set();
    assert.equal(t.module.canStart(PLAN, none, false), false);
    assert.equal(t.module.canStart(PLAN, new Set(["kube-system/Deployment/coredns"]), false), false);
    assert.equal(t.module.canStart(PLAN, new Set(["kube-system/Deployment/coredns"]), true), true);
    assert.equal(t.module.canStart({ ...PLAN, canApprove: false }, new Set(["kube-system/Deployment/coredns"]), true), false);
    await t.panel.begin();                                                               // not allowed yet: nothing is sent
    assert.equal(t.calls.some((c) => c[0] === "POST" && c[1] === "/runs"), false);
});

test("approving sends the digest of the plan that was shown, the acknowledgements, and no name", async () => {
    const run = { runKey: "loop-1", status: "RUNNING", mode: "LAB", lab: { nodes: ["desktop-worker"], stage: "INJECT_FAILURE" }, monitor: { live: null, episodes: [], comparisons: [] }, timing: { elapsedS: 3 } };
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run: null } }, "POST /plan": { body: PLAN }, "POST /runs": { body: run } });
    await t.panel.load(); t.panel.form.nodes.add("desktop-worker"); await t.panel.preview();
    t.panel.state().ack.add("kube-system/Deployment/coredns"); t.panel.state().desktopAck = true;
    await t.panel.begin();
    const sent = t.calls.find((c) => c[1] === "/runs")[2];
    assert.equal(sent.planDigest, "d1"); assert.deepEqual(sent.acknowledgedWorkloads, ["kube-system/Deployment/coredns"]); assert.equal(sent.desktopAcknowledged, true);
    assert.deepEqual(Object.keys(sent).sort(), ["acknowledgedWorkloads", "desktopAcknowledged", "plan", "planDigest"]);      // no person anywhere in the approval
    assert.equal(find(t.root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-restore").length, 1);        // Restore now is on screen while it runs
});

test("a finished run offers the report, to open or to download, and another test", async () => {
    const run = { runKey: "loop-9", status: "COMPLETED", statusLabel: "Completed", mode: "LAB", lab: { nodes: ["desktop-worker"] }, monitor: { live: null, episodes: [], comparisons: [] }, safety: { level: "ok" } };
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run } } });
    await t.panel.load();
    const links = find(t.root, (n) => n.tag === "a").map((n) => n.getAttribute("href"));
    assert.ok(links.includes(`${BASE}/runs/loop-9/report`)); assert.ok(links.includes(`${BASE}/runs/loop-9/report?download=true`));
    assert.equal(find(t.root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-another").length, 1);
});

test("a worker that could not be restored shows the exact command, never 'restored'", async () => {
    const run = { runKey: "loop-2", status: "NEEDS_MANUAL_RESTORE", statusLabel: "Needs your attention", mode: "LAB", lab: { nodes: ["desktop-worker"] },
        monitor: { live: null, episodes: [], comparisons: [] }, safety: { level: "alert", message: "Something could not be restored.", actions: ["docker start desktop-worker"] } };
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run } } });
    await t.panel.load();
    assert.match(text(t.root), /docker start desktop-worker/); assert.doesNotMatch(text(t.root), /confirmed restored/i);
});

test("an error from the server is shown and nothing is claimed started", async () => {
    const t = setup({ "GET /targets": { body: TARGETS }, "GET /current": { body: { run: null } }, "POST /plan": { ok: false, status: 409, body: { detail: { message: "Choose at least one worker." } } } });
    await t.panel.load(); t.panel.form.nodes.add("desktop-worker"); await t.panel.preview();
    assert.match(text(t.root), /Choose at least one worker/); assert.equal(t.panel.state().phase, "setup");
});
