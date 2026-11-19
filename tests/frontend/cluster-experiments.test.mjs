// Experiments and Accuracy of the connected cluster: only what the server returns is drawn, nothing is filled in.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document } from "./support/mini-dom.mjs";

const SCRIPT = readFileSync(new URL("../../app/static/js/cluster-experiments.js", import.meta.url), "utf8");

function setup(body, { ok = true } = {}) {
    const doc = new Document();
    const experiments = doc.createElement("div"); experiments.setAttribute("data-role", "ce-experiments"); doc.body.append(experiments);
    const accuracy = doc.createElement("div"); accuracy.setAttribute("data-role", "ce-accuracy"); doc.body.append(accuracy);
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set, Date, Promise };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "cluster-experiments.js" });
    const fetchImpl = async () => ({ ok, json: async () => body });
    return { module: sandbox.window.ResilienceClusterExperiments, doc, experiments, accuracy, fetchImpl };
}
const text = (n) => (n.textContent || "") + (n.children || []).map(text).join(" ");

const row = (over = {}) => ({ source: "validation-run", id: "r/1", failedNode: "w2", at: "2026-11-18T10:03:00+00:00", withChange: true, status: "PASS",
    predictedDown: ["details"], observedDown: ["details"], match: true, workflowHeld: true, checkPassed: null, recoveryVerified: true, durationS: 70, reasons: [], ...over });
const body = (over = {}) => ({ connected: true, cluster: { state: "connected", context: "docker-desktop" }, experiments: [row(), row({ id: "r/2", failedNode: "w3", observedDown: ["ratings"], match: false })],
    accuracy: { experimentsCompared: 2, matched: 1, agreement: 0.5, byNode: [{ node: "w2", compared: 1, matched: 1 }, { node: "w3", compared: 1, matched: 0 }], differences: [row({ failedNode: "w3", match: false, observedDown: ["ratings"] })], note: "N" },
    elsewhere: { count: 0, clusters: [] }, unattributed: { count: 0 }, ...over });

test("each recorded failure is a card with the model's prediction beside the measurement", async () => {
    const t = setup(body());
    await t.module.load(t.doc, t.fetchImpl);
    const words = text(t.experiments);
    assert.match(words, /w2 was stopped/); assert.match(words, /Model predicted down/); assert.match(words, /Measured down/);
    assert.match(words, /Approved test run/); assert.match(words, /Matched/); assert.match(words, /Different/);
    assert.match(words, /Measured, not simulated/);
});

test("accuracy is a count of matches, and says when there are too few to be a rate", async () => {
    const t = setup(body());
    await t.module.load(t.doc, t.fetchImpl);
    assert.match(text(t.accuracy), /1 of 2/); assert.match(text(t.accuracy), /too few to call an accuracy rate/);
    assert.match(text(t.accuracy), /Where the model and the measurement differed/);
});

test("with nothing recorded the tabs say so and say how to record one, instead of drawing zeros", async () => {
    const t = setup(body({ experiments: [], accuracy: { experimentsCompared: 0, matched: 0, agreement: null, byNode: [], differences: [], note: "N" } }));
    await t.module.load(t.doc, t.fetchImpl);
    assert.match(text(t.experiments), /No node failure has been recorded on this cluster yet/);
    assert.match(text(t.experiments), /Prove it on a test cluster/); assert.match(text(t.experiments), /Watch a real failure/);
    assert.match(text(t.accuracy), /^—|—/); assert.doesNotMatch(text(t.accuracy), /100%|0%/);
});

test("records saved from other clusters are mentioned but never listed as this cluster's", async () => {
    const t = setup(body({ experiments: [], elsewhere: { count: 3, clusters: ["u9"] }, accuracy: { experimentsCompared: 0, matched: 0, agreement: null, byNode: [], differences: [], note: "N" } }));
    await t.module.load(t.doc, t.fetchImpl);
    assert.match(text(t.experiments), /3 experiments are saved from other clusters/); assert.doesNotMatch(text(t.experiments), /was stopped/);
});

test("when the cluster is not connected nothing is drawn as an experiment", async () => {
    const t = setup({ connected: false, cluster: { state: "not-connected" }, experiments: [], accuracy: null, elsewhere: { count: 2 }, unattributed: { count: 0 } });
    await t.module.load(t.doc, t.fetchImpl);
    assert.doesNotMatch(text(t.experiments), /was stopped/); assert.doesNotMatch(text(t.accuracy), /of \d/);
});

test("a request that fails says so and invents nothing", async () => {
    const t = setup({}, { ok: false });
    await t.module.load(t.doc, t.fetchImpl);
    assert.match(text(t.experiments), /could not be read/); assert.match(text(t.accuracy), /could not be read/);
});
