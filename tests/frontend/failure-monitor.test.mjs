// The failure monitor draws measured samples as they are: gaps stay gaps, nothing is smoothed, and a missing before-run is not "better".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document } from "./support/mini-dom.mjs";

const SCRIPT = readFileSync(new URL("../../app/static/js/failure-monitor.js", import.meta.url), "utf8");

function load() {
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "failure-monitor.js" });
    return sandbox.window.ResilienceFailureMonitor;
}
const text = (node) => (node.textContent || "") + (node.children || []).map(text).join(" ");
const find = (node, pred, out = []) => { if (pred(node)) out.push(node); (node.children || []).forEach((c) => find(c, pred, out)); return out; };
const cls = (name) => (n) => String(n.className || "").split(/\s+/).includes(name);

function timeline() {
    const samples = [];
    for (let t = 0; t < 12; t += 1) {
        samples.push({ t, ready: { web: t >= 4 && t < 8 ? 0 : 1, db: 3 }, nodes: { w1: !(t >= 5 && t < 9) },
            req: t === 3 ? null : { ok: !(t >= 5 && t < 7), ms: t >= 5 && t < 7 ? null : 40, status: 200, why: t >= 5 && t < 7 ? "HTTP 503" : null },
            cpu: { w2: t === 6 ? null : 100 + t }, mem: { w2: 800 } });
    }
    return { intervalS: 1, baseline: { web: 1, db: 3 }, samples, marks: [{ t: 4, kind: "stop", detail: "w1" }, { t: 8, kind: "restore", detail: "w1" }, { t: 9, kind: "node-ready", detail: "w1" }],
        events: [{ t: 5, reason: "NodeNotReady", object: "Node/w1", message: "Node w1 status is now: NodeNotReady" }], logs: [], reads: { services: { ok: 12, failed: 1 } }, sources: {}, truncated: false };
}
const SUMMARY = { evidence: "MEASURED", servicesDown: ["web"], servicesDegraded: [], recoveryAfterRestoreS: 3, requests: { windowSuccessRatio: 0.5, windowFailed: 2, windowTotal: 4, longestOutageS: 2, firstFailureS: 1 } };

test("an availability lane per service, coloured by how many replicas were ready, with a grey cell where nothing was read", () => {
    const m = load(); const doc = new Document(); const root = doc.createElement("div");
    const t = timeline(); t.samples[2].ready = null;
    m.render(doc, root, { live: null, episodes: [{ kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1, summary: SUMMARY, predictionCheck: null }], comparisons: [] },
        { selected: m.episodeKey({ kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1 }), timelineFor: () => t });
    assert.ok(find(root, cls("fm-cell--down")).length > 0, "a service with no ready replica is red");
    assert.ok(find(root, cls("fm-cell--ok")).length > 0);
    assert.equal(find(root, (n) => cls("fm-cell--unk")(n) && n.tag === "rect").length, 2, "web and db are each unread once");
});

test("a failed request is drawn red and an unreadable CPU reading is a gap in the line, not a zero", () => {
    const m = load();
    const t = timeline();
    const runs = m.segments(t.samples, (s) => (s.cpu || {}).w2);
    assert.equal(runs.length, 2);                                      // t=6 is missing: two runs, not one line through a made-up point
    assert.equal(runs[0].at(-1).t, 5); assert.equal(runs[1][0].t, 7);
    assert.equal(m.laneTone(null), "unk"); assert.equal(m.laneTone(0), "down"); assert.equal(m.laneTone(0.5), "part"); assert.equal(m.laneTone(1), "ok");
});

test("the tiles say 'not measured' for a figure that was not measured", () => {
    const m = load(); const doc = new Document(); const root = doc.createElement("div");
    m.render(doc, root, { live: null, episodes: [{ kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1, summary: { ...SUMMARY, requests: null, recoveryAfterRestoreS: null, servicesDegraded: [] }, predictionCheck: null }], comparisons: [] },
        { selected: m.episodeKey({ kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1 }), timelineFor: () => null });
    assert.match(text(root), /not measured/);
    assert.match(text(root), /Loading the measured samples/);        // the samples are being fetched; no chart is faked meanwhile
});

test("without a measured baseline no before-and-after is claimed, and the person is told how to get one", () => {
    const m = load(); const doc = new Document(); const root = doc.createElement("div");
    const episode = { kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1, summary: SUMMARY, predictionCheck: null };
    m.render(doc, root, { live: null, episodes: [episode], comparisons: [{ candidateId: "c1", worker: "w1", repeat: 1, measuredBefore: false, measuredAfter: true, improved: null, rows: [] }] },
        { selected: m.episodeKey(episode), timelineFor: () => timeline() });
    assert.match(text(root), /was not measured, so no before-and-after is claimed/);
    assert.doesNotMatch(text(root), /improved/);
});

test("a measured before and after is a table with a verdict per row", () => {
    const m = load(); const doc = new Document(); const root = doc.createElement("div");
    const episode = { kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1, summary: SUMMARY, predictionCheck: null };
    const rows = [{ id: "servicesDown", label: "Services that lost every ready replica", unit: "count", before: 1, after: 0, verdict: "better" },
        { id: "longestOutage", label: "Longest stretch", unit: "seconds", before: 8, after: 8, verdict: "same" }];
    m.render(doc, root, { live: null, episodes: [episode], comparisons: [{ candidateId: "c1", worker: "w1", repeat: 1, measuredBefore: true, measuredAfter: true, improved: true, rows }] },
        { selected: m.episodeKey(episode), timelineFor: () => timeline() });
    assert.match(text(root), /Better/); assert.match(text(root), /Same/);
    assert.match(text(root), /one observation, not an accuracy figure/);
});

test("the model's prediction sits in its own table, with differences named", () => {
    const m = load(); const doc = new Document(); const root = doc.createElement("div");
    const check = { predictedDown: [], observedDown: ["web"], predictedButStayedUp: [], wentDownButNotPredicted: ["web"], otherServicesDown: [], matches: false, note: "n" };
    m.render(doc, root, { live: null, episodes: [{ kind: "candidate", candidateId: "c1", worker: "w1", repeat: 1, summary: SUMMARY, predictionCheck: check }], comparisons: [] },
        { selected: "candidate|c1|w1|1", timelineFor: () => timeline() });
    assert.match(text(root), /Different/); assert.match(text(root), /Lost, but not predicted: web/);
});

test("the live failure is offered first and labelled live; with nothing recorded the page waits instead of drawing an empty chart", () => {
    const m = load(); const doc = new Document();
    const waiting = doc.createElement("div");
    m.render(doc, waiting, { live: null, episodes: [], comparisons: [] }, { running: true });
    assert.match(text(waiting), /first failure has not started yet/);
    const root = doc.createElement("div");
    m.render(doc, root, { live: { worker: "w1", kind: "candidate", stage: "INJECT_FAILURE", timeline: timeline(), summary: SUMMARY }, episodes: [], comparisons: [] }, { running: true });
    assert.match(text(root), /Now · w1/); assert.match(text(root), /Live — stage: inject failure/);
});
