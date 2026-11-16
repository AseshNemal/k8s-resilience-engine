// Logic tests for app/static/js/workflow-names.js. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/workflow-names.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, Map, Set, Array, Object, String, Number });
const names = window.ResilienceWorkflowNames;
const detail = (...workflows) => ({ portfolio: { workflows } });

test("a workflow with a name is shown by it; one without keeps its id", () => {
    names.use(detail({ workflowId: "9e28bd30405f", name: "verify-manual-productpage" }, { workflowId: "productpage-view", name: null }));
    assert.equal(names.label("9e28bd30405f"), "verify-manual-productpage");
    assert.equal(names.label("productpage-view"), "productpage-view");
    assert.equal(names.label("unknown-id"), "unknown-id", "an id the engine never named is shown as it is");
});

test("two workflows with one name are told apart by a short id suffix", () => {
    names.use(detail({ workflowId: "aaaaaa111", name: "Checkout" }, { workflowId: "bbbbbb222", name: "Checkout" }));
    assert.equal(names.label("aaaaaa111"), "Checkout (aaaaaa)");
    assert.equal(names.label("bbbbbb222"), "Checkout (bbbbbb)");
});

test("a name equal to another workflow's id does not hide which is which", () => {
    names.use(detail({ workflowId: "x1", name: "y1" }, { workflowId: "y1", name: null }));
    assert.equal(names.label("x1"), "y1 (x1)");
    assert.equal(names.label("y1"), "y1", "an unnamed workflow keeps its id");
});

test("no detail, or one without a portfolio, leaves every id as it is", () => {
    names.use(null);
    assert.equal(names.label("a"), "a");
    names.use({});
    assert.equal(names.label("a"), "a");
});
