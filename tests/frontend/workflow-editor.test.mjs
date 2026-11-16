import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const window = {};
vm.runInNewContext(readFileSync(new URL("../../app/static/js/workflow-editor.js", import.meta.url), "utf8"), {
    window,
    document: { getElementById: () => null },
});
const create = window.ResilienceWorkflowEditor.createModel;

test("graph edits support undo and redo without losing dependencies", () => {
    const m = create();
    m.add("orders");
    m.add("payments", 300, 50);
    m.connect("orders", "payments");
    assert.equal(m.state().edges[0].dependency, "Unknown");
    m.change((s) => (s.edges[0].dependency = "Mandatory"));
    m.remove("payments");
    assert.equal(m.state().edges.length, 0);
    m.undo();
    assert.equal(m.state().edges[0].dependency, "Mandatory");
    m.redo();
    assert.equal(m.state().edges.length, 0);
});

test("merging observed calls preserves user labels and records provenance sessions", () => {
    const m = create({
        nodes: [
            { service: "orders", x: 10, y: 10 },
            { service: "payments", x: 300, y: 10 },
        ],
        edges: [{ source: "orders", target: "payments", dependency: "Optional" }],
    });
    const candidate = {
        services: ["orders", "payments", "unmapped"],
        entryService: "orders",
        name: "Checkout",
        edges: [
            { source: "orders", target: "payments" },
            { source: "orders", target: "unmapped" },
        ],
    };
    m.merge(candidate, "recording-1", ["orders", "payments"]);
    assert.equal(m.state().edges.length, 1);
    assert.equal(m.state().edges[0].dependency, "Optional");
    assert.equal(m.state().nodes.length, 2);
    assert.equal(m.state().recordingIds[0], "recording-1");
    m.undo();
    assert.equal(m.state().recordingIds.length, 0);
});

test("loading a saved record strips read-only proof fields and does not duplicate services", () => {
    const m = create({
        edges: [
            {
                source: "a",
                target: "b",
                dependency: "Mandatory",
                evidence: ["trace"],
            },
        ],
        experimentallyValidated: true,
    });
    assert.equal(m.state().experimentallyValidated, undefined);
    assert.equal(window.ResilienceWorkflowEditor.editable(m.state()).edges[0].evidence, undefined);
    assert.equal(m.state().edges[0].evidence[0], "trace");
    m.add("a");
    m.add("a");
    assert.equal(m.state().nodes.length, 1);
    m.connect("a", "a");
    assert.equal(m.state().edges.length, 1);
});

test("service classification changes only existing incoming calls and keeps uncertainty explicit", () => {
    const m = create();
    m.add("entry");
    m.add("helper");
    m.add("unconnected");
    m.connect("entry", "helper");
    m.change((s) => {
        s.entryService = "entry";
        window.ResilienceWorkflowEditor.classifyService(s, "unconnected", "Mandatory");
    });
    assert.equal(m.state().edges.length, 1);
    assert.equal(m.state().edges[0].dependency, "Unknown");
    m.change((s) => window.ResilienceWorkflowEditor.classifyService(s, "helper", "Supporting"));
    assert.equal(m.state().edges[0].dependency, "Supporting");
    assert.equal(window.ResilienceWorkflowEditor.reviewIssues(m.state())[0].service, "unconnected");
});

test("review catches unknown calls and disconnected nodes without changing the saved graph", () => {
    const m = create();
    m.add("entry");
    m.add("worker");
    m.connect("entry", "worker");
    m.change((s) => {
        s.entryService = "entry";
    });
    assert.equal(window.ResilienceWorkflowEditor.reviewIssues(m.state())[0].edge, 0);
    m.change((s) => window.ResilienceWorkflowEditor.classifyService(s, "worker", "Optional"));
    assert.equal(window.ResilienceWorkflowEditor.reviewIssues(m.state()).length, 0);
    m.undo();
    assert.equal(m.state().edges[0].dependency, "Unknown");
});

test("manual, recording and test evidence combine without resetting positions or labels", () => {
    const m = create({
        name: "Current workflow",
        entryService: "orders",
        nodes: [
            { service: "orders", x: 80, y: 90 },
            { service: "payments", x: 400, y: 90 },
        ],
        edges: [{ source: "orders", target: "payments", dependency: "Optional" }],
    });
    const candidate = {
        name: "Checkout test",
        entryService: "orders",
        services: ["orders", "payments"],
        edges: [
            {
                source: "orders",
                target: "payments",
                dependency: "Unknown",
                evidence: [{ traceId: "abc", callerSpanId: "a", calleeSpanId: "b" }],
                declarations: [{ dependency: "Mandatory" }],
            },
        ],
    };
    const recording = { kind: "recording", id: "recording", version: 2, candidateId: "operation" };
    const tested = { kind: "test", id: "tests", version: 1, candidateId: "test-execution" };
    m.merge(
        { ...candidate, edges: [{ ...candidate.edges[0], declarations: [] }] },
        "recording",
        ["orders", "payments"],
        recording,
    );
    m.merge(candidate, "tests", ["orders", "payments"], tested);
    assert.equal(m.state().name, "Current workflow");
    assert.equal(m.state().nodes[0].x, 80);
    assert.equal(m.state().nodes.length, 2);
    assert.equal(m.state().edges.length, 1);
    assert.equal(m.state().edges[0].dependency, "Optional");
    assert.equal(m.state().edges[0].evidence.length, 2);
    assert.equal(m.state().evidenceRefs.length, 2);
    assert.equal(window.ResilienceWorkflowEditor.conflicting(m.state().edges[0]).length, 1);
    m.change((s) => window.ResilienceWorkflowEditor.classifyService(s, "payments", "Optional"));
    assert.equal(window.ResilienceWorkflowEditor.reviewIssues(m.state()).length, 0);
    m.undo();
    assert.equal(window.ResilienceWorkflowEditor.conflicting(m.state().edges[0]).length, 1);
    m.undo();
    assert.equal(m.state().evidenceRefs.length, 1);
    assert.equal(m.state().edges[0].evidence.length, 1);
    m.redo();
    assert.equal(m.state().edges[0].evidence.length, 2);
});

test("new evidence cannot overwrite a validated label or silently erase its call", () => {
    const m = create({
        nodes: [
            { service: "a", x: 1, y: 1 },
            { service: "b", x: 250, y: 1 },
        ],
        edges: [
            {
                source: "a",
                target: "b",
                dependency: "Mandatory",
                classificationBasis: "experimentally_validated",
                evidence: [{ traceId: "validated" }],
            },
        ],
    });
    m.merge(
        {
            services: ["a", "b"],
            edges: [{ source: "a", target: "b", dependency: "Optional", evidence: [{ traceId: "test" }] }],
        },
        "tests",
        ["a", "b"],
        { kind: "test", id: "test", version: 1, candidateId: "case" },
    );
    m.change((s) => window.ResilienceWorkflowEditor.classifyService(s, "b", "Optional"));
    m.remove("b");
    assert.equal(m.state().nodes.length, 2);
    assert.equal(m.state().edges[0].dependency, "Mandatory");
    assert.equal(m.state().edges[0].classificationBasis, "experimentally_validated");
    assert.equal(m.state().edges[0].evidence.length, 2);
    assert.equal(window.ResilienceWorkflowEditor.editable(m.state()).edges[0].classificationBasis, undefined);
});
