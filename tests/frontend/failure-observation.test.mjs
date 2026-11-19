// Logic tests for app/static/js/failure-observation.js. Run with: node --test tests/frontend/
// Predicted, observed and "not observed" stay apart; the panel only reads, and only starts when the person presses Start.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/failure-observation.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, console, setTimeout, clearTimeout });
const panelModule = window.ResilienceFailureObservation;
const plain = (value) => JSON.parse(JSON.stringify(value));

class Node {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.listeners = {}; this.textContent = ""; this.disabled = false; }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = [...nodes]; }
    setAttribute(key, value) { this.attributes[key] = value; }
    addEventListener(name, fn) { this.listeners[name] = fn; }
}
const fakeDocument = { createElement: (tag) => new Node(tag) };
const walk = (node) => [node, ...node.children.flatMap(walk)];
const text = (node) => walk(node).map((n) => n.textContent).join(" | ");
const button = (root, role) => walk(root).find((n) => n.attributes && n.attributes["data-role"] === role);

const watching = { observationId: "obs-1", status: "WATCHING", active: true, watched: ["w1", "w2"], failedNode: null, timeline: [], reads: { ok: 1, failed: 0 },
    comparison: { state: "NO_FAILURE_SEEN" } };
const finished = { ...watching, status: "RECOVERED", active: false, failedNode: "w1", timeline: [{ at: "2026-11-17T10:00:05+00:00", kind: "node-ready", detail: "w1 is Ready again." }],
    comparison: { state: "DIFFERENT", node: "w1", predictedServicesDown: ["details"], observedServicesDown: ["reviews"], predictedServicesDegraded: [], observedServicesDegraded: [],
        workflows: { observed: false, message: "Workflow outcomes were not observed, only service readiness." } } };

test("nothing watched offers a start and nothing else", () => {
    const model = plain(panelModule.describe(null));
    assert.equal(model.canStart, true);
    assert.equal(model.canStop, false);
});

test("a watch with no failure yet says nothing failed, and offers a stop", () => {
    const model = plain(panelModule.describe(watching));
    assert.match(model.headline, /Nothing has failed yet/);
    assert.deepEqual([model.canStart, model.canStop], [false, true]);
});

test("a finished watch shows predicted and observed apart, and a difference is called a difference", () => {
    const model = plain(panelModule.describe(finished));
    const rows = Object.fromEntries(model.rows.map((r) => [r.service, r]));
    assert.deepEqual([rows.details.predicted, rows.details.observed, rows.details.agree], ["down", "stays up", false]);
    assert.deepEqual([rows.reviews.predicted, rows.reviews.observed, rows.reviews.agree], ["stays up", "down", false]);
    assert.equal(model.tone, "warn");
    assert.match(model.headline, /differs from the prediction/);
});

test("workflow outcomes are reported as not observed, never as survived", () => {
    const model = plain(panelModule.describe(finished));
    assert.ok(model.notes.some((n) => /not observed/.test(n)));
    assert.ok(!/surviv/i.test(model.notes.join(" ")));
});

test("unreadable checks are named as unknown, not healthy", () => {
    const model = plain(panelModule.describe({ ...watching, reads: { ok: 1, failed: 2, lastError: "CLUSTER_UNREADABLE" } }));
    assert.ok(model.notes.some((n) => /unknown, not healthy/.test(n)));
});

test("the panel starts only on the button, records who started it, and keeps polling only while active", async () => {
    const calls = [], scheduled = [];
    const root = new Node("div");
    const answers = [{ observation: null }];
    const fetchImpl = async (url, init = {}) => {
        calls.push([url, init.method || "GET", init.headers]);
        const body = init.method === "POST" ? watching : answers.shift();
        return { ok: true, json: async () => body };
    };
    const timers = { set: (fn, ms) => { scheduled.push(ms); return scheduled.length; }, clear: () => {} };
    const panel = panelModule.createPanel({ root, document: fakeDocument, fetchImpl, timers, namespace: () => "bookinfo", operator: () => "asesh" });

    await panel.refresh();
    assert.deepEqual(calls.map((c) => c[1]), ["GET"], "reading the current state never starts a watch");
    assert.equal(scheduled.length, 0, "nothing polls while nothing is watched");

    await button(root, "fo-start").listeners.click();
    const post = calls.find((c) => c[1] === "POST");
    assert.equal(post[0], "/api/recommendations/failure-observations");
    assert.equal(post[2]["X-Operator-Name"], "asesh");
    assert.deepEqual(scheduled, [panelModule.POLL_MS], "it polls while a watch is active");
    assert.match(text(root), /Stop watching/);
});

test("a refusal from the server is shown, not swallowed", async () => {
    const root = new Node("div");
    const fetchImpl = async () => ({ ok: false, status: 409, json: async () => ({ detail: { message: "A failure is already being watched." } }) });
    const panel = panelModule.createPanel({ root, document: fakeDocument, fetchImpl, timers: { set: () => 1, clear: () => {} } });
    await panel.start();
    assert.match(text(root), /already being watched/);
});

test("while watching, the person is told what the model predicts for each worker before they stop one", () => {
    const model = plain(panelModule.describe({ ...watching, predicted: { w1: { servicesDown: ["details"], servicesDegraded: ["reviews"] }, w2: { servicesDown: [], servicesDegraded: [] } } }));
    assert.deepEqual(model.expectations.map((e) => e.node), ["w1", "w2"]);
    assert.match(model.expectations[0].text, /details would lose every ready replica; reviews would lose some/);
    assert.match(model.expectations[1].text, /no service would lose a ready replica/);
});

test("the panel says it is checking until the first read, and a poll that finds nothing new redraws nothing", async () => {
    const root = new Node("div");
    let replaced = 0;
    const original = root.replaceChildren.bind(root);
    root.replaceChildren = (...nodes) => { replaced++; original(...nodes); };
    const fetchImpl = async () => ({ ok: true, json: async () => ({ observation: watching }) });
    const panel = panelModule.createPanel({ root, document: fakeDocument, fetchImpl, timers: { set: () => 1, clear: () => {} } });
    assert.match(text(root), /Checking for a watch in progress/);
    assert.ok(!button(root, "fo-start"), "no start button while it is not yet known whether a watch is running");
    await panel.refresh();
    const afterFirst = replaced;
    await panel.refresh();
    await panel.refresh();
    assert.equal(replaced, afterFirst, "identical polls draw nothing, so a screen reader is not told the same thing every few seconds");
});

test("a watch whose cluster is not the connected one says nothing is being recorded", () => {
    const model = plain(panelModule.describe({ ...watching, clusterMatch: "other" }));
    assert.match(model.headline, /not the one this watch started on, so nothing is being recorded/);
    assert.equal(model.tone, "warn");
    assert.equal(model.canStop, true, "the person can still end it");
});

test("a watch's measured timeline is the live failure while it runs and one finished failure afterwards, and is absent when nothing was measured", () => {
    const timeline = { samples: [{ t: 0, ready: { a: 1 }, nodes: { w1: true } }], marks: [{ t: 0, kind: "begin" }], baseline: { a: 1 } };
    const monitor = { timeline, summary: { servicesDown: [] }, requests: false };
    const live = plain(panelModule.monitorModel({ ...watching, status: "NODE_DOWN", failedNode: "w1", monitor }));
    assert.equal(live.live.kind, "watch"); assert.equal(live.live.worker, "w1"); assert.deepEqual(live.episodes, []);
    const done = plain(panelModule.monitorModel({ ...finished, monitor }));
    assert.equal(done.live, null); assert.equal(done.episodes[0].kind, "watch"); assert.equal(done.episodes[0].predictionCheck, null);
    assert.equal(panelModule.monitorModel({ ...watching, monitor: { timeline: { samples: [] } } }), null);
    assert.equal(panelModule.monitorModel(watching), null);
});

test("starting a watch sends the page address only when one was typed", async () => {
    const sent = [];
    const fetchImpl = async (url, init = {}) => { if (init.method === "POST") sent.push(JSON.parse(init.body)); return { ok: true, status: 200, json: async () => ({ ...watching }) }; };
    const root = new Node("div");
    const panel = panelModule.createPanel({ root, document: fakeDocument, fetchImpl, timers: { set: () => 1, clear: () => {} }, namespace: () => "bookinfo" });
    await panel.start();
    assert.equal(sent[0].requestUrl, null);
});
