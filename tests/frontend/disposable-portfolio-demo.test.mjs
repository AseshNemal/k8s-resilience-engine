import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(
    new URL("../../experiments/failure_impact_validation/static/disposable_portfolio.js", import.meta.url),
    "utf8",
);

test("research API requests are relative to the mounted page", () => {
    assert.ok(source.includes('postJson("api/synthetic/generate"'));
    assert.ok(source.includes('simulate("api/synthetic/fail-one"'));
    assert.ok(source.includes('simulate("api/synthetic/fail-all"'));
    assert.ok(!source.includes('postJson("/api/synthetic/'));
});

function load() {
    const window = {};
    vm.runInNewContext(source, { window, console });
    return window.DisposablePortfolioDemo;
}

test("cross-layer summaries preserve each workflow graph", () => {
    const demo = load();
    const summaries = demo.graphSummaries({
        graphs: [
            {
                workflowId: "checkout",
                nodes: [{}, {}, {}],
                edges: [{}, {}],
                mandatoryServices: ["cartservice"],
                optionalServices: ["emailservice"],
                unresolvedServices: [],
            },
            {
                workflowId: "notifications",
                nodes: [{}, {}],
                edges: [{}],
                mandatoryServices: ["emailservice"],
                optionalServices: [],
                unresolvedServices: [],
            },
        ],
    });

    assert.deepEqual(
        JSON.parse(JSON.stringify(summaries)),
        [
            {
                workflowId: "checkout",
                nodeCount: 3,
                edgeCount: 2,
                mandatoryServices: ["cartservice"],
                optionalServices: ["emailservice"],
                unresolvedServices: [],
            },
            {
                workflowId: "notifications",
                nodeCount: 2,
                edgeCount: 1,
                mandatoryServices: ["emailservice"],
                optionalServices: [],
                unresolvedServices: [],
            },
        ],
    );
});

test("cross-layer graph layout preserves the five research layers and real edges", () => {
    const demo = load();
    const graph = {
        workflowId: "checkout",
        nodes: [
            { identifier: "workflow:checkout", nodeType: "WORKFLOW", label: "checkout" },
            { identifier: "service:cart", nodeType: "SERVICE", label: "cart" },
            { identifier: "pod:cart-1", nodeType: "POD", label: "cart-1" },
            { identifier: "node:worker-1", nodeType: "NODE", label: "worker-1" },
            { identifier: "failure-domain:zone-a", nodeType: "FAILURE_DOMAIN", label: "zone-a" },
        ],
        edges: [
            { source: "workflow:checkout", target: "service:cart", relationship: "REQUIRES_MANDATORY" },
            { source: "service:cart", target: "pod:cart-1", relationship: "HAS_REPLICA" },
            { source: "pod:cart-1", target: "node:worker-1", relationship: "RUNS_ON" },
            { source: "node:worker-1", target: "failure-domain:zone-a", relationship: "IN_FAILURE_DOMAIN" },
        ],
        mandatoryServices: ["cart"],
        optionalServices: [],
        unresolvedServices: [],
    };

    const layout = demo.graphLayout(graph);
    assert.deepEqual(
        JSON.parse(JSON.stringify(layout.layers.map((layer) => layer.type))),
        ["WORKFLOW", "SERVICE", "POD", "NODE", "FAILURE_DOMAIN"],
    );
    assert.equal(layout.edges.length, graph.edges.length);
    assert.ok(layout.layers.every((layer, index, layers) => index === 0 || layer.x > layers[index - 1].x));

    const markup = demo.renderCrossLayerGraph(graph, 0);
    assert.match(markup, /<svg/);
    assert.match(markup, /checkout cross-layer graph/);
    assert.match(markup, /graph-node--workflow/);
    assert.match(markup, /graph-edge--requires-mandatory/);
});

test("result rows keep single-node and all-node portfolio metrics", () => {
    const demo = load();
    const rows = demo.resultRows({
        results: [
            {
                failedNode: "node-1",
                failedWorkflows: ["checkout", "cart-management"],
                failedServices: ["cartservice"],
                blastRadius: 50,
                survivability: 50,
            },
            {
                failedNode: "node-3",
                failedWorkflows: ["email-notification"],
                failedServices: ["emailservice"],
                blastRadius: 25,
                survivability: 75,
            },
        ],
    });

    assert.deepEqual(JSON.parse(JSON.stringify(rows)), [
        {
            failedNode: "node-1",
            failedWorkflowCount: 2,
            failedWorkflows: ["checkout", "cart-management"],
            failedServices: ["cartservice"],
            blastRadius: 50,
            survivability: 50,
        },
        {
            failedNode: "node-3",
            failedWorkflowCount: 1,
            failedWorkflows: ["email-notification"],
            failedServices: ["emailservice"],
            blastRadius: 25,
            survivability: 75,
        },
    ]);
});

test("node risk ranking preserves the server ranking and failure evidence", () => {
    const demo = load();
    const ranking = demo.nodeRiskRanking({
        results: [{
            nodeRiskRanking: [
                { node: "node-1", blastRadius: 50, failedWorkflows: ["checkout", "cart"] },
                { node: "node-3", blastRadius: 25, failedWorkflows: ["email"] },
                { node: "node-2", blastRadius: 0, failedWorkflows: [] },
            ],
        }],
    });

    assert.deepEqual(JSON.parse(JSON.stringify(ranking)), [
        { rank: 1, node: "node-1", blastRadius: 50, failedWorkflows: ["checkout", "cart"] },
        { rank: 2, node: "node-3", blastRadius: 25, failedWorkflows: ["email"] },
        { rank: 3, node: "node-2", blastRadius: 0, failedWorkflows: [] },
    ]);
});
