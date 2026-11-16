// Logic tests for app/static/js/placement-map.js: the failure rule drawn on the map must be the engine's rule.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/placement-map.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, console });
const map = window.ResiliencePlacement;
// Objects built inside the vm context have another realm's prototypes; compare their plain data.
const plain = (value) => JSON.parse(JSON.stringify(value));

const row = (service, ...pairs) => ({ service, readyReplicas: pairs.reduce((sum, [, count]) => sum + count, 0), nodes: pairs.map(([node, readyPods]) => ({ node, readyPods })) });
const placement = [row("details", ["a", 1]), row("reviews", ["a", 1], ["b", 1], ["c", 1]), row("ratings", ["b", 2]), row("idle")];

test("a service is down only when every ready replica ran on the stopped node", () => {
    assert.deepEqual(plain(map.outcome(placement[0], "a")), { state: "down", left: 0, of: 1 });
    assert.deepEqual(plain(map.outcome(placement[2], "b")), { state: "down", left: 0, of: 2 });
    assert.equal(map.outcome(placement[2], "a").state, "ok");
});

test("a service that loses some replicas is degraded and reports what is left", () => {
    assert.deepEqual(plain(map.outcome(placement[1], "a")), { state: "degraded", left: 2, of: 3 });
});

test("a service with no ready replica is not blamed on the node", () => {
    assert.equal(map.outcome(placement[3], "a").state, "none");
    assert.deepEqual(plain(map.impact(placement, "a")), { down: ["details"], degraded: ["reviews"] });
});

test("no stopped node means nothing is affected", () => {
    assert.deepEqual(plain(map.impact(placement, null)), { down: [], degraded: [] });
});

test("services keep one colour by sorted name, whatever order they are listed in", () => {
    const one = map.hues(["reviews", "details", "ratings"]);
    const two = map.hues(["ratings", "reviews", "details", "details"]);
    assert.deepEqual(plain(one), plain(two));
    assert.notEqual(one.details, one.ratings);
});

test("a deployment graph is reduced to ready pods per node and not-ready pods are not counted", () => {
    const graph = { services: [{ name: "b", pods: [{ node: "n1", ready: true }, { node: "n1", ready: true }, { node: "n2", ready: false }] }, { name: "a", pods: [] }] };
    assert.deepEqual(plain(map.fromDeployment(graph)), [
        { service: "a", readyReplicas: 0, nodes: [] },
        { service: "b", readyReplicas: 2, nodes: [{ node: "n1", readyPods: 2 }] },
    ]);
});
