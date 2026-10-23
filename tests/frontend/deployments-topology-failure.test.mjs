// Topology tab: what one node failure does to each service (click a node to fail it).
// Run with: node --test tests/frontend/deployments-topology-failure.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_topology_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console });
const { simulateFailure } = window.DeploymentsTopologyWidget;

const pod = (node, ready = true) => ({ node, ready });
const services = [
    { name: "packed", pods: [pod("w2"), pod("w2")] },
    { name: "spread", pods: [pod("w1"), pod("w2"), pod("w3")] },
    { name: "elsewhere", pods: [pod("w3")] },
    { name: "never-ready", pods: [pod("w2", false)] },
];

test("a service with every ready replica on the failed node goes down", () => {
    assert.deepEqual([...simulateFailure(services, "w2").down], ["packed"]);
});

test("a service with replicas elsewhere keeps running with fewer", () => {
    const { degraded } = simulateFailure(services, "w2");
    assert.equal(degraded.length, 1);
    assert.equal(degraded[0].name, "spread");
    assert.equal(degraded[0].left, 2);
    assert.equal(degraded[0].of, 3);
});

test("services with no ready replica on the node are unaffected, and never-ready ones are not counted", () => {
    const sim = simulateFailure(services, "w2");
    assert.equal(sim.unaffected, 1);  // "elsewhere"; "never-ready" had nothing ready to lose
    assert.ok(!sim.down.includes("never-ready"));
});
