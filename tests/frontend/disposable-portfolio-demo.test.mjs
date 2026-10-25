import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(
    new URL("../../experiments/failure_impact_validation/static/disposable_portfolio.js", import.meta.url),
    "utf8",
);

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
