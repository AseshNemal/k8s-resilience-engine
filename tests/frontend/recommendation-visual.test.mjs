// The pictures of recommendations use the engine's failure rule, and say what they show.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document } from "./support/mini-dom.mjs";

const SCRIPT = readFileSync(new URL("../../app/static/js/recommendation-visual.js", import.meta.url), "utf8");

function load() {
    const sandbox = { window: {}, console, Math, JSON, Number, String, Object, Array, Set };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "recommendation-visual.js" });
    return sandbox.window.ResilienceVisual;
}
const row = (service, ...nodes) => ({ service, readyReplicas: nodes.reduce((n, [, c]) => n + c, 0), nodes: nodes.map(([node, readyPods]) => ({ node, readyPods })) });
const text = (node) => (node.textContent || "") + (node.children || []).map(text).join(" ");
const find = (node, pred, out = []) => { if (pred(node)) out.push(node); (node.children || []).forEach((c) => find(c, pred, out)); return out; };

test("a service with every replica on the failed node goes offline; some replicas left is degraded; none on it is fine", () => {
    const v = load();
    assert.equal(v.outcome(row("db", ["w1", 3]), "w1").state, "down");
    const spread = v.outcome(row("db", ["w1", 1], ["w2", 2]), "w1");
    assert.equal(spread.state, "degraded"); assert.equal(spread.left, 2); assert.equal(spread.of, 3);
    assert.equal(v.outcome(row("db", ["w2", 2]), "w1").state, "ok");
    assert.equal(v.outcome(row("db"), "w1").state, "none");           // no ready replica today is not "down because of this node"
});

test("the words say what happens, with the numbers", () => {
    const v = load();
    assert.equal(v.outcomeText("db", { state: "down" }), "db goes offline");
    assert.equal(v.outcomeText("db", { state: "degraded", left: 2, of: 3 }), "db keeps 2 of 3");
    assert.equal(v.outcomeText("db", { state: "ok", of: 3 }), "db keeps all 3");
});

test("each strategy has a plain name; an unknown one is still readable", () => {
    const v = load();
    assert.equal(v.strategyInfo("TOPOLOGY_SPREAD").name, "Spread across nodes");
    assert.equal(v.strategyInfo("REPLICA_INCREASE_PLUS_2").name, "Add a replica");
    assert.equal(v.strategyInfo("SOMETHING_NEW").name, "Something new");
});

test("the card draws the same nodes now and after, marks the second as simulated, and breaks the failed node in both", () => {
    const v = load();
    const doc = new Document();
    const option = { service: "db", type: "TOPOLOGY_SPREAD", parts: [{ service: "db" }],
        afterPlacement: [row("db", ["w1", 1], ["w2", 1], ["w3", 1])] };
    const card = v.changeCard(doc, option, [row("db", ["w1", 3])], ["w1", "w2", "w3"], "w1");
    const words = text(card);
    assert.match(words, /Observed/); assert.match(words, /Simulated/);
    assert.match(words, /db goes offline/); assert.match(words, /db keeps 2 of 3/);
    const broken = find(card, (n) => String(n.className || "").includes("rv-node--failed"));
    assert.equal(broken.length, 2);                                    // once per picture
    assert.equal(find(card, (n) => String(n.className || "").includes("rv-pod--sim")).length, 3);   // only the "after" pods are dashed
});

test("with no node picked it says how to pick one instead of inventing an outcome", () => {
    const v = load();
    const doc = new Document();
    const card = v.changeCard(doc, { service: "db", type: "TOPOLOGY_SPREAD", afterPlacement: [row("db", ["w1", 1], ["w2", 2])] }, [row("db", ["w1", 3])], ["w1", "w2"], null);
    assert.match(text(card), /Pick a worker/);
    assert.doesNotMatch(text(card), /goes offline/);
});

test("a change the engine gave no placement for is said, not drawn", () => {
    const v = load();
    const doc = new Document();
    const card = v.changeCard(doc, { service: "db", type: "TOPOLOGY_SPREAD" }, [row("db", ["w1", 3])], ["w1"], "w1");
    assert.match(text(card), /did not return a placement/);
});
