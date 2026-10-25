// Logic tests for app/static/js/recommendation-workflow-risk.js. Run with: node --test tests/frontend/
// The grid draws the server's per-workflow prediction; an unknown result is never drawn as "survives".
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-workflow-risk.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, console });
const risk = window.ResilienceWorkflowRisk;
const plain = (value) => JSON.parse(JSON.stringify(value));

const today = [{ workflowId: "checkout", criticality: 0.9, breaksAt: ["w1"] }, { workflowId: "browse", criticality: 0.3, breaksAt: ["w2"] },
               { workflowId: "admin", criticality: 0.1, breaksAt: [] }];
const nodes = ["w1", "w2", "w3"];
const fix = { workflows: { protected: ["checkout"], exposed: ["browse"], exposedAt: { browse: ["w2"] } } };

test("today's grid says which worker breaks which workflow, and a workflow that breaks nowhere survives every worker", () => {
    const model = plain(risk.buildModel(today, nodes, null, "w1"));
    const row = Object.fromEntries(model.workflows.map((w) => [w.id, w.today]));
    assert.deepEqual(row.checkout, { w1: "breaks", w2: "survives", w3: "survives" });
    assert.deepEqual(row.admin, { w1: "survives", w2: "survives", w3: "survives" });
    assert.equal(model.atRiskToday, 2);
    assert.equal(model.candidateKnown, false);
});

test("the selected fix turns protected workflows green and keeps the ones it leaves exposed red", () => {
    const model = plain(risk.buildModel(today, nodes, fix, "w1"));
    const after = Object.fromEntries(model.workflows.map((w) => [w.id, w.after]));
    assert.deepEqual(after.checkout, { w1: "survives", w2: "survives", w3: "survives" });
    assert.deepEqual(after.browse, { w1: "survives", w2: "breaks", w3: "survives" });
    assert.equal(model.stillAtRisk, 1);
    assert.match(risk.summaryText(model), /2 of 3 workflows would break.*1 would still break and 1 would be protected/);
});

test("a fix with no prediction is drawn as not known, never as surviving", () => {
    const model = plain(risk.buildModel(today, nodes, { workflows: { protected: [], exposed: null, exposedAt: null } }, null));
    assert.ok(model.workflows.every((w) => Object.values(w.after).every((state) => state === "unknown")));
    assert.equal(model.afterKnown, false);
    assert.match(risk.summaryText(model), /not known/);
});

test("a worker named only in the server's list still gets a column, and nothing is drawn without workflows", () => {
    const model = plain(risk.buildModel([{ workflowId: "checkout", criticality: 0.9, breaksAt: ["w9"] }], ["w1"], null, null));
    assert.deepEqual(model.nodes, ["w1", "w9"]);
    assert.equal(risk.buildModel([], nodes, fix, null), null);
    assert.equal(risk.buildModel(null, nodes, fix, null), null);
});

function node(tag) { return { tag, children: [], attributes: {}, textContent: "", className: "",
    setAttribute(k, v) { this.attributes[k] = v; }, append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; } }; }
const walk = (root) => [root, ...root.children.flatMap(walk)];

test("every cell states its result in words and only the 'after' grid appears once a fix is selected", () => {
    const withFix = node("div");
    risk.render(withFix, today, nodes, fix, { document: { createElement: node }, failedNode: "w1" });
    const words = walk(withFix).filter((n) => n.attributes["data-state"]).map((n) => `${n.attributes["data-state"]}:${n.children.map((c) => c.textContent).join("")}`);
    assert.ok(words.includes("breaks:✕Breaks") && words.includes("survives:✓Survives"));
    assert.equal(walk(withFix).filter((n) => n.tag === "table").length, 2);
    const without = node("div");
    risk.render(without, today, nodes, null, { document: { createElement: node } });
    assert.equal(walk(without).filter((n) => n.tag === "table").length, 1);
});
