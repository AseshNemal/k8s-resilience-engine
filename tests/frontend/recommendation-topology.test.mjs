// Logic tests for app/static/js/recommendation-topology.js. Run with: node --test tests/frontend/
// The view draws only the two graphs it is given; it estimates nothing and says so when there is no prediction.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-topology.js", import.meta.url), "utf8");

function load() {
    const window = {};
    vm.runInNewContext(source, { window, console });
    return window.ResilienceTopologyCompare;
}
const topo = load();
const plain = (value) => JSON.parse(JSON.stringify(value));

const pod = (name, node, ready = true) => ({ name, node, ready });
const graph = (services, nodes = ["worker", "worker2"]) => ({ nodes: nodes.map((name) => ({ name })), services });
const now = graph([
    { name: "reviews", declaredReplicas: 2, readyReplicas: 2, pods: [pod("reviews-a", "worker"), pod("reviews-b", "worker")] },
    { name: "details", declaredReplicas: 1, readyReplicas: 1, pods: [pod("details-a", "worker")] },
]);
const after = graph([
    { name: "reviews", declaredReplicas: 3, readyReplicas: 3, pods: [pod("reviews-a", "worker"), pod("reviews-b", "worker2"), pod("reviews-added-1", "worker2")] },
    { name: "details", declaredReplicas: 1, readyReplicas: 1, pods: [pod("details-a", "worker")] },
]);

function chips(side, node) {
    return side.nodes.find((n) => n.name === node).chips.map((c) => `${c.pod}:${c.state}`);
}

test("a pod with the same name on another node moved, a new name was added", () => {
    const model = plain(topo.buildModel(now, after, { failedNode: "worker" }));
    assert.deepEqual(chips(model.now, "worker"), ["reviews-a:stays", "reviews-b:moves", "details-a:stays"]);
    assert.deepEqual(chips(model.after, "worker2"), ["reviews-b:moves", "reviews-added-1:added"]);
    assert.deepEqual(model.changes, { moved: 1, added: 1, removed: 0 });
    assert.equal(model.now.nodes.find((n) => n.name === "worker").failed, true);
    assert.equal(model.now.nodes.find((n) => n.name === "worker2").failed, false);
});

test("a moved pod remembers where it came from and where it goes", () => {
    const model = plain(topo.buildModel(now, after));
    assert.equal(model.now.nodes[0].chips[1].otherNode, "worker2");
    assert.equal(model.after.nodes[1].chips[0].otherNode, "worker");
});

test("a service has the same colour on both sides", () => {
    const model = plain(topo.buildModel(now, after));
    const colour = model.services.find((s) => s.name === "reviews").colour;
    assert.equal(model.now.nodes[0].chips[0].colour, colour);
    assert.equal(model.after.nodes[1].chips[0].colour, colour);
});

test("without a simulated graph only the observed placement is described, and nothing is predicted", () => {
    const model = plain(topo.buildModel(now, null));
    assert.equal(model.after, null);
    assert.match(topo.summaryText(model), /only the observed placement/);
});

test("without a current graph there is nothing to compare", () => {
    assert.equal(topo.buildModel(null, after), null);
    assert.equal(topo.buildModel({}, after), null);
});

test("a pod that is not ready stays marked as not ready", () => {
    const sick = graph([{ name: "ratings", declaredReplicas: 2, readyReplicas: 1, pods: [pod("r-a", "worker"), pod("r-b", "worker2", false)] }]);
    const model = plain(topo.buildModel(sick, null));
    assert.equal(model.now.nodes[1].chips[0].ready, false);
});

test("a node only a pod names is still drawn, and no capacity is invented", () => {
    const odd = graph([{ name: "a", pods: [pod("a-1", "ghost")] }], ["worker"]);
    const model = plain(topo.buildModel(odd, null));
    assert.deepEqual(model.now.nodes.map((n) => n.name), ["worker", "ghost"]);
    assert.equal(JSON.stringify(model).includes("cpu"), false);
});

test("an identical prediction says so", () => {
    assert.match(topo.summaryText(topo.buildModel(now, now)), /same as the observed/);
});
