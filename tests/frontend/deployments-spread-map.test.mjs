// Exposed services tab: the services × nodes spread map.
// Run with: node --test tests/frontend/deployments-spread-map.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_drift_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console });
const { spreadMatrix } = window.DeploymentsDriftWidget;

const pod = (node, ready = true) => ({ node, ready });
const graph = {
    nodes: [{ name: "w1" }, { name: "w2" }, { name: "w3" }],
    services: [
        { name: "spread", pods: [pod("w1"), pod("w2"), pod("w3")], exposedFailureDomains: [] },
        { name: "packed", pods: [pod("w2"), pod("w2"), pod(null, false)], exposedFailureDomains: ["w2"] },
        { name: "down", pods: [pod("w1", false)], exposedFailureDomains: [] },
    ],
};

test("each service's pods are counted per node, ready and not ready apart", () => {
    const { rows } = spreadMatrix(graph);
    const packed = rows.find((r) => r.name === "packed");
    assert.equal(packed.cells.w2.ready, 2);
    assert.equal(packed.cells.__pending__.notReady, 1);
    assert.equal(packed.ready, 2);
});

test("the status comes from the graph's exposure, and a service with no ready pod is unavailable", () => {
    const status = Object.fromEntries(spreadMatrix(graph).rows.map((r) => [r.name, r.status]));
    assert.equal(status.spread, "safe");
    assert.equal(status.packed, "exposed");
    assert.equal(status.down, "unavailable");
});

test("exposed services come first and pods with no node get a last column", () => {
    const { rows, columns } = spreadMatrix(graph);
    assert.equal(rows[0].name, "packed");
    assert.deepEqual([...columns], ["w1", "w2", "w3", "__pending__"]);  // copied out of the vm context
});
