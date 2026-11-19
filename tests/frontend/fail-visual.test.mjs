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
