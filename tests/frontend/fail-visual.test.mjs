// The "Fail a node" pictures and page: workers drawn with their pods, a control plane locked, unknown state never drawn as running.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document } from "./support/mini-dom.mjs";

const read = (name) => readFileSync(new URL(`../../app/static/js/${name}`, import.meta.url), "utf8");
const BASE = "/api/recommendations/validation-loop";
const text = (n) => (n.textContent || "") + (n.children || []).map(text).join(" ");
const find = (n, pred, out = []) => { if (pred(n)) out.push(n); (n.children || []).forEach((c) => find(c, pred, out)); return out; };
const cls = (n, c) => (n.className || "").split(/\s+/).includes(c);

function load() {
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set, Promise };
    sandbox.window.document = undefined;
    vm.createContext(sandbox);
    vm.runInContext(read("fail-visual.js"), sandbox, { filename: "fail-visual.js" });
    vm.runInContext(read("failure-lab.js"), sandbox, { filename: "failure-lab.js" });
    return sandbox.window;
}

const PLACEMENT = [{ service: "details", nodes: [{ node: "w1", readyPods: 2 }] }, { service: "reviews", nodes: [{ node: "w1", readyPods: 1 }, { node: "w2", readyPods: 2 }] }];

test("pods of a node come from the observed placement, and nothing is invented", () => {
    const { ResilienceFailVisual: V } = load();
    assert.deepEqual(JSON.parse(JSON.stringify(V.podsOf(PLACEMENT, "w1"))), [{ service: "details", count: 2 }, { service: "reviews", count: 1 }]);
    assert.deepEqual(JSON.parse(JSON.stringify(V.podsOf(PLACEMENT, "w9"))), []);
    assert.deepEqual(JSON.parse(JSON.stringify(V.podsOf(null, "w1"))), []);
});

test("a tile is picked, locked, or (while running) up, down or unknown: never running by default", () => {
    const { ResilienceFailVisual: V } = load();
    assert.equal(V.tileState({}, { picked: true }), "picked");
    assert.equal(V.tileState({}, { picked: false }), "pick");
    assert.equal(V.tileState({}, { picked: false, preview: true }), "keep");
    assert.equal(V.tileState({}, { locked: true, picked: true }), "locked");
    assert.equal(V.tileState({}, { running: true, stopped: true }), "down");
    assert.equal(V.tileState({}, { running: true, stopped: false }), "up");
    assert.equal(V.tileState({}, { running: true, stopped: null }), "unknown");
});

test("the live state is read from the newest sample that has one, and unreadable stays unknown", () => {
    const { ResilienceFailVisual: V } = load();
    const run = { monitor: { live: { timeline: { baseline: { details: 2, reviews: 3 }, samples: [
        { t: 0, ready: { details: 2, reviews: 3 }, nodes: { w1: true } }, { t: 2, ready: { details: 0, reviews: 3 }, nodes: { w1: false } }, { t: 4, ready: null, nodes: null }] } } } };
    assert.deepEqual(JSON.parse(JSON.stringify(V.liveNodes(run))), { w1: false });
    const services = JSON.parse(JSON.stringify(V.liveServices(run)));
    assert.deepEqual(services.map((s) => [s.service, s.state]), [["details", "down"], ["reviews", "ok"]]);
    assert.deepEqual(JSON.parse(JSON.stringify(V.liveNodes({}))), {});
    assert.deepEqual(JSON.parse(JSON.stringify(V.liveServices({}))), []);
});

test("the picker draws every node, locks the control plane, and toggles only workers", () => {
    const { ResilienceFailVisual: V } = load();
    const doc = new Document();
    const picks = [];
    const row = V.nodeTiles(doc, { nodes: [{ name: "cp", locked: true }, { name: "w1" }, { name: "w2" }], placement: PLACEMENT, hues: {}, picked: new Set(["w2"]), running: false, onToggle: (n) => picks.push(n) });
    const tiles = find(row, (n) => n.getAttribute && n.getAttribute("data-node"));
    assert.deepEqual(tiles.map((t) => t.tag), ["div", "button", "button"]);
    assert.match(text(tiles[0]), /Never stopped here/);
    assert.match(text(tiles[2]), /Will stop/);
    assert.equal(find(tiles[1], (n) => cls(n, "fv-pod")).length, 3);
    tiles[1].click();
    tiles[0].click && tiles[0].click();
    assert.deepEqual(picks, ["w1"]);
});

test("the visual page reads the application, offers only workers, and still previews exactly what was chosen", async () => {
    const window = load();
    const doc = new Document(); const root = doc.createElement("div"); doc.body.append(root);
    const calls = [];
    const routes = {
        "GET /api/failure-impacts/overview": { namespace: "bookinfo", namespaces: ["bookinfo"], services: PLACEMENT },
        [`GET ${BASE}/targets`]: { targets: [{ kind: "DOCKER_DESKTOP", label: "Docker Desktop", available: true, warning: "x", identity: { nodes: ["cp-control-plane", "w1", "w2"] } }] },
        [`GET ${BASE}/current`]: { run: null },
        [`POST ${BASE}/plan`]: { canApprove: true, planDigest: "d", acknowledgementsNeeded: [], issues: [], notes: [], target: { kind: "DOCKER_DESKTOP" },
            lab: { nodes: ["w1"], holdS: 60, allWorkersStopped: false, impact: { services: [{ service: "details", ready: 2, lost: 2, left: 0, state: "down" }] } } },
    };
    const fetchImpl = async (url, init = {}) => {
        const method = init.method || "GET"; calls.push([method, url, init.body ? JSON.parse(init.body) : null]);
        const body = routes[`${method} ${url.split("?")[0]}`];
        return body ? { ok: true, status: 200, json: async () => body } : { ok: false, status: 404, json: async () => ({}) };
    };
    const panel = window.ResilienceFailureLab.createPanel({ root, document: doc, fetchImpl, visual: true, timers: { set: () => 1, clear: () => {} } });
    await panel.load();
    const tiles = find(root, (n) => n.getAttribute && n.getAttribute("data-node"));
    assert.deepEqual(tiles.map((t) => t.getAttribute("data-node")), ["cp-control-plane", "w1", "w2"]);
    assert.equal(tiles[0].tag, "div");                                       // the control plane cannot be picked
    assert.ok(calls.every(([method]) => method === "GET"));                    // loading only reads
    panel.form.nodes.add("w1");
    await panel.preview();
    const sent = calls.find((c) => c[0] === "POST")[2];
    assert.equal(sent.mode, "LAB"); assert.deepEqual(sent.nodes, ["w1"]); assert.equal(sent.namespace, "bookinfo");
    assert.match(text(root), /details/); assert.match(text(root), /goes offline/);
    assert.match(text(root), /Keeps running/);                                // the workers that were not picked say so
});

test("a typed time is whole seconds inside the bounds, and the reason is said in words when it is not", () => {
    const { ResilienceFailureLab: L } = load();
    const ok = (a, u) => JSON.parse(JSON.stringify(L.parseHold(a, u)));
    assert.deepEqual(ok("90", "seconds"), { seconds: 90, problem: null });
    assert.deepEqual(ok("2.5", "minutes"), { seconds: 150, problem: null });
    assert.deepEqual(ok("30", "minutes"), { seconds: 1800, problem: null });
    assert.match(ok("5", "seconds").problem, /at least 10/);
    assert.match(ok("31", "minutes").problem, /at most 30 minutes/);
    assert.match(ok("", "seconds").problem, /Type how long/);
    assert.match(ok("abc", "seconds").problem, /greater than zero/);
    assert.match(ok("-3", "minutes").problem, /greater than zero/);
    assert.equal(L.humanSeconds(60), "1 minute"); assert.equal(L.humanSeconds(150), "2 minutes 30 seconds"); assert.equal(L.humanSeconds(45), "45 seconds");
    assert.equal(L.canPreview({ nodes: new Set(["w"]), hold: 1800 }), true);
    assert.equal(L.canPreview({ nodes: new Set(["w"]), hold: 1801 }), false);
    assert.equal(L.canPreview({ nodes: new Set(["w"]), hold: NaN }), false);
});

test("the Custom time is typed, validated as it is typed, and sent exactly", async () => {
    const window = load();
    const doc = new Document(); const root = doc.createElement("div"); doc.body.append(root);
    const calls = [];
    const routes = {
        "GET /api/failure-impacts/overview": { namespace: "bookinfo", namespaces: ["bookinfo"], services: PLACEMENT },
        [`GET ${BASE}/targets`]: { targets: [{ kind: "DOCKER_DESKTOP", label: "Docker Desktop", available: true, warning: "x", identity: { nodes: ["w1", "w2"] } }] },
        [`GET ${BASE}/current`]: { run: null },
        [`POST ${BASE}/plan`]: { canApprove: true, planDigest: "d", acknowledgementsNeeded: [], issues: [], notes: [], target: { kind: "DISPOSABLE" },
            lab: { nodes: ["w1"], holdS: 150, allWorkersStopped: false, impact: { services: [] } } },
    };
    const fetchImpl = async (url, init = {}) => {
        const method = init.method || "GET"; calls.push([method, url, init.body ? JSON.parse(init.body) : null]);
        const body = routes[`${method} ${url.split("?")[0]}`];
        return body ? { ok: true, status: 200, json: async () => body } : { ok: false, status: 404, json: async () => ({}) };
    };
    const panel = window.ResilienceFailureLab.createPanel({ root, document: doc, fetchImpl, visual: true, timers: { set: () => 1, clear: () => {} } });
    await panel.load();
    panel.form.nodes.add("w1");
    find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-custom")[0].click();
    const amount = find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-custom-amount")[0];
    const preview = () => find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-preview")[0];
    amount.value = "5"; amount.dispatchEvent({ type: "input" });
    assert.equal(preview().disabled, true);                                   // too short: the button waits, and the reason is shown
    assert.match(text(root), /at least 10 seconds/);
    const unit = find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-custom-unit")[0];
    amount.value = "2.5"; unit.value = "minutes"; unit.dispatchEvent({ type: "change" });
    assert.equal(preview().disabled, false);
    assert.match(text(root), /2 minutes 30 seconds/);
    await panel.preview();
    assert.equal(calls.find((c) => c[0] === "POST")[2].holdSeconds, 150);
});

test("when no cluster can be tested the page says why for each one and offers to look again", async () => {
    const window = load();
    const doc = new Document(); const root = doc.createElement("div"); doc.body.append(root);
    let reads = 0;
    const fetchImpl = async (url) => {
        if (url.includes("/overview")) return { ok: true, status: 200, json: async () => ({ namespace: "bookinfo", services: [] }) };
        if (url.endsWith("/targets")) { reads += 1; return { ok: true, status: 200, json: async () => ({ targets: [
            { kind: "DOCKER_DESKTOP", label: "Docker Desktop cluster", available: false, reason: "No cluster is connected." },
            { kind: "DISPOSABLE", label: "Disposable test cluster", available: false, reason: "The disposable test cluster has not been prepared." }] }) }; }
        return { ok: true, status: 200, json: async () => ({ run: null }) };
    };
    const panel = window.ResilienceFailureLab.createPanel({ root, document: doc, fetchImpl, visual: true, timers: { set: () => 1, clear: () => {} } });
    await panel.load();
    assert.match(text(root), /No cluster can be tested right now/);
    assert.match(text(root), /Docker Desktop cluster|My cluster/); assert.match(text(root), /No cluster is connected\./);
    assert.match(text(root), /has not been prepared/);
    find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-recheck")[0].click();
    await new Promise((r) => setTimeout(r, 0)); await new Promise((r) => setTimeout(r, 0));
    assert.equal(reads, 2);
});

// ---- a running test says what the test knows, not what the lagging cluster still reports ----------------------------------------

test("a worker the test has stopped is never drawn as Running while Kubernetes still reads it Ready", () => {
    const V = load().ResilienceFailVisual;
    assert.equal(V.runPhase({ status: "RUNNING", lab: { stage: "INJECT_FAILURE" } }), "stopping");
    assert.equal(V.runPhase({ status: "RUNNING", lab: { stage: "PROBE" } }), "holding");
    assert.equal(V.runPhase({ status: "RUNNING", lab: { stage: "RECOVER_WORKER" } }), "restoring");
    assert.equal(V.runPhase({ status: "ABORTING", lab: { stage: "PROBE" } }), "restoring");
    assert.equal(V.tileState({}, { running: true, phase: "stopping", stopped: false }), "stopping");
    assert.equal(V.tileState({}, { running: true, phase: "holding", stopped: false }), "held");      // cluster still says Ready
    assert.equal(V.tileState({}, { running: true, phase: "holding", stopped: null }), "held");
    assert.equal(V.tileState({}, { running: true, phase: "holding", stopped: true }), "down");       // cluster agrees
    assert.equal(V.tileState({}, { running: true, phase: "restoring", stopped: true }), "returning");
    assert.equal(V.tileState({}, { running: true, phase: "restoring", stopped: false }), "up");
});

test("the cluster's service counts are marked not updated while its workers still read Ready", () => {
    const V = load().ResilienceFailVisual;
    const run = (nodes) => ({ status: "RUNNING", lab: { stage: "PROBE", nodes: ["w1", "w2"] },
        monitor: { live: { timeline: { baseline: { details: 1 }, samples: [{ t: 1, ready: { details: 1 }, nodes }] } } } });
    assert.equal(V.awaitingCluster(run({ w1: true, w2: true })), true);
    assert.equal(V.awaitingCluster(run({ w1: false, w2: true })), true);        // one still reads Ready
    assert.equal(V.awaitingCluster(run({ w1: false, w2: false })), false);      // the cluster has noticed every worker
    assert.equal(V.awaitingCluster({ status: "COMPLETED", lab: { stage: "DONE", nodes: ["w1"] } }), false);
});

async function panelWithRun(run) {
    const window = load();
    const doc = new Document(); const root = doc.createElement("div"); doc.body.append(root);
    const routes = {
        "GET /api/failure-impacts/overview": { namespace: "bookinfo", namespaces: ["bookinfo"], services: PLACEMENT },
        [`GET ${BASE}/targets`]: { targets: [{ kind: "DOCKER_DESKTOP", label: "Docker Desktop", available: true, warning: "x", identity: { nodes: ["cp-control-plane", "w1", "w2"] } }] },
        [`GET ${BASE}/current`]: { run },
    };
    const fetchImpl = async (url, init = {}) => {
        const body = routes[`${init.method || "GET"} ${url.split("?")[0]}`];
        return body ? { ok: true, status: 200, json: async () => body } : { ok: false, status: 404, json: async () => ({}) };
    };
    const panel = window.ResilienceFailureLab.createPanel({ root, document: doc, fetchImpl, visual: true, timers: { set: () => 1, clear: () => {} } });
    await panel.load();
    return root;
}

test("a running test shows a plain banner with Restore now in it, stopped workers as stopped, and counts marked not updated", async () => {
    const live = { timeline: { baseline: { details: 2, reviews: 3 }, samples: [{ t: 3, ready: { details: 2, reviews: 3 }, nodes: { w1: true, w2: true } }] } };
    const root = await panelWithRun({ runKey: "r1", status: "RUNNING", statusLabel: "Running", mode: "LAB", timing: { elapsedS: 12 },
        lab: { nodes: ["w1", "w2"], holdS: 300, stage: "PROBE" }, monitor: { live, episodes: [] } });
    const status = find(root, (n) => cls(n, "fl-status"))[0];
    assert.match(text(status), /2 workers are stopped/);
    assert.match(text(status), /Held for 5 minutes/);
    assert.equal(find(status, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-restore").length, 1);   // Restore is in the banner, not at the bottom
    const tiles = find(root, (n) => n.getAttribute && n.getAttribute("data-node"));
    assert.match(text(tiles[1]), /Stopped \(cluster not updated yet\)/); assert.doesNotMatch(text(tiles[1]), /Running/);
    assert.match(text(root), /keeps showing them as Ready for about a minute/);
    assert.match(text(root), /2 of 2 · not updated yet/);
    assert.doesNotMatch(text(root), /2 of 2 ready/);
});

test("a finished test puts the next step directly under the result and says in words what happened", async () => {
    const summary = { nodeNotReadyAfterS: 60, servicesDown: ["details"], services: { details: { downS: 27.1 } }, recoveryAfterRestoreS: 7.7, requests: null };
    const root = await panelWithRun({ runKey: "r1", status: "INTERRUPTED", statusLabel: "Stopped early", stopReason: "The hold was ended early on request; every worker was restored.", mode: "LAB",
        lab: { nodes: ["w1"], holdS: 300, stage: "DONE" }, monitor: { live: null, episodes: [{ kind: "baseline", worker: "w1+w2", repeat: 1, summary }] } });
    const body = root.children[0];
    const order = body.children.map((n) => n.className || "");
    const statusAt = order.findIndex((c) => c.includes("fl-status")), actionsAt = order.findIndex((c) => c.includes("fl-row--top")), outcomeAt = order.findIndex((c) => c.includes("fl-outcome"));
    assert.ok(statusAt >= 0 && actionsAt === statusAt + 1, "the actions follow the banner immediately");
    const another = find(root, (n) => n.getAttribute && n.getAttribute("data-role") === "fl-another")[0];
    assert.ok(cls(another, "primary-button"), "Run another test is the main button");
    assert.ok(outcomeAt > actionsAt);
    assert.match(text(root), /Kubernetes noticed the stopped workers after 60 seconds/);
    assert.match(text(root), /details lost every ready replica \(offline about 27 seconds/);
    assert.match(text(root), /back 8 seconds after/);
    assert.match(text(root), /No requests were sent to your application/);
});

test("when the run ended before Kubernetes noticed, the page says so instead of reporting no outage", async () => {
    const root = await panelWithRun({ runKey: "r1", status: "INTERRUPTED", statusLabel: "Stopped early", mode: "LAB", lab: { nodes: ["w1"], holdS: 300, stage: "DONE" },
        monitor: { live: null, episodes: [{ kind: "baseline", worker: "w1", repeat: 1, summary: { nodeNotReadyAfterS: null, servicesDown: [], services: {}, recoveryAfterRestoreS: null, requests: null } }] } });
    assert.match(text(root), /never marked a stopped worker NotReady/);
    assert.match(text(root), /choose 2 minutes or more/);
    assert.doesNotMatch(text(root), /No service lost every ready replica/);
});

function loadMonitor() {
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set, Promise, Infinity };
    vm.createContext(sandbox);
    vm.runInContext(read("failure-monitor.js"), sandbox, { filename: "failure-monitor.js" });
    return sandbox.window.ResilienceFailureMonitor;
}

test("a test with no change applied names the stopped workers, never 'before the change'", () => {
    const M = loadMonitor();
    const monitor = { live: null, episodes: [{ kind: "baseline", candidateId: null, worker: "w1+w2", repeat: 1 }] };
    assert.match(M.choices(monitor, { lab: true })[0].label, /^Stopped workers: w1, w2$/);
    assert.match(M.choices(monitor)[0].label, /^Before the change/);                    // other pages keep their wording
});

test("the availability chart draws the stretch between the stop and Kubernetes noticing as stopped, not as up", () => {
    const M = loadMonitor();
    const doc = new Document();
    const ready = (n) => ({ details: n });
    const timeline = { intervalS: 2, baseline: { details: 1 }, samples: [
        { t: 0, ready: ready(1), nodes: {} }, { t: 4, ready: ready(1), nodes: {} }, { t: 30, ready: ready(1), nodes: {} }, { t: 64, ready: ready(0), nodes: {} }, { t: 98, ready: ready(1), nodes: {} }],
        marks: [{ t: 4, kind: "stop", detail: "" }, { t: 63, kind: "node-not-ready", detail: "w1" }, { t: 90, kind: "restore", detail: "" }] };
    const holder = doc.createElement("div");
    M.render(doc, holder, { live: null, episodes: [{ kind: "baseline", worker: "w1", repeat: 1, summary: { nodeNotReadyAfterS: 59, servicesDown: ["details"], services: {}, requests: null, recoveryAfterRestoreS: 8 } }] },
        { lab: true, selected: "baseline||w1|1", timelineFor: () => timeline });
    const cells = find(holder, (n) => n.tag === "rect" && (n.className || n.getAttribute("class") || "").toString().includes("fm-cell"));
    const tones = cells.map((c) => (c.getAttribute("class") || c.className || "").toString().replace("fm-cell fm-cell--", ""));
    assert.ok(tones.includes("stale"), "cells after the stop and before Kubernetes noticed are stale");
    assert.ok(tones.indexOf("stale") > tones.indexOf("ok"), "the cells before the stop are still plain up");
    assert.match(text(holder), /stopped, Kubernetes had not noticed yet/);
});
