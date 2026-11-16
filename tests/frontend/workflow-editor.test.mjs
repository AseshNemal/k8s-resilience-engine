import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";
const window = {};
vm.runInNewContext(readFileSync(new URL("../../app/static/js/workflow-editor.js", import.meta.url), "utf8"), { window, document: { getElementById: () => null } });
const create = window.ResilienceWorkflowEditor.createModel;

test("graph edits support undo and redo without losing dependencies", () => {
    const m = create(); m.add("orders"); m.add("payments", 300, 50); m.connect("orders", "payments");
    assert.equal(m.state().edges[0].dependency, "Unknown");
    m.change(s => s.edges[0].dependency = "Mandatory");
    m.remove("payments"); assert.equal(m.state().edges.length, 0);
    m.undo(); assert.equal(m.state().edges[0].dependency, "Mandatory");
    m.redo(); assert.equal(m.state().edges.length, 0);
});

test("merging observed calls preserves user labels and records provenance sessions", () => {
    const m = create({ nodes: [{ service: "orders", x: 10, y: 10 }, { service: "payments", x: 300, y: 10 }], edges: [{ source: "orders", target: "payments", dependency: "Optional" }] });
    const candidate = { services: ["orders", "payments", "unmapped"], entryService: "orders", name: "Checkout", edges: [{ source: "orders", target: "payments" }, { source: "orders", target: "unmapped" }] };
    m.merge(candidate, "recording-1", ["orders", "payments"]);
    assert.equal(m.state().edges.length, 1); assert.equal(m.state().edges[0].dependency, "Optional");
    assert.equal(m.state().nodes.length, 2); assert.equal(m.state().recordingIds[0], "recording-1");
    m.undo(); assert.equal(m.state().recordingIds.length, 0);
});

test("loading a saved record strips read-only proof fields and does not duplicate services", () => {
    const m = create({ edges: [{ source: "a", target: "b", dependency: "Mandatory", evidence: ["trace"] }], experimentallyValidated: true });
    assert.equal(m.state().experimentallyValidated, undefined); assert.equal(m.state().edges[0].evidence, undefined);
    m.add("a"); m.add("a"); assert.equal(m.state().nodes.length, 1);
    m.connect("a", "a"); assert.equal(m.state().edges.length, 1);
});
