// A <th>/<td> whose CSS class sets `display` stops being a table cell, and its row collapses. This broke the Workflows-at-risk grid and the
// saved-test comparison; both now put such styling on an inner element. This guards every table those two views draw.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
const classesThatSetDisplay = new Set();
for (const [, selectors, body] of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!/(^|;|\s)display\s*:/.test(body)) continue;
    for (const match of selectors.matchAll(/\.([A-Za-z0-9_-]+)/g)) classesThatSetDisplay.add(match[1]);
}

function node(tag) { return { tag, children: [], attributes: {}, listeners: {}, textContent: "", className: "", type: "",
    setAttribute(k, v) { this.attributes[k] = v; }, append(...n) { this.children.push(...n); }, replaceChildren(...n) { this.children = n; },
    addEventListener(k, v) { this.listeners[k] = v; } }; }
const walk = (root) => [root, ...root.children.flatMap(walk)];
const cells = (root) => walk(root).filter((n) => n.tag === "th" || n.tag === "td");
const offenders = (root) => cells(root).filter((n) => (n.className || "").split(/\s+/).some((c) => classesThatSetDisplay.has(c)))
    .map((n) => `${n.tag}.${n.className}`);

test("the stylesheet's display-setting classes are found (the guard itself works)", () => {
    assert.ok(classesThatSetDisplay.has("rec-wf__cell") && classesThatSetDisplay.has("cmp-head"));
});

test("the Workflows-at-risk grid draws no table cell with a display-setting class", () => {
    const window = {};
    vm.runInNewContext(readFileSync(new URL("../../app/static/js/recommendation-workflow-risk.js", import.meta.url), "utf8"), { window });
    const root = node("div");
    window.ResilienceWorkflowRisk.render(root, [{ workflowId: "a", criticality: 0.9, breaksAt: ["w1"] }], ["w1", "w2"],
        { workflows: { exposedAt: {} } }, { document: { createElement: node }, failedNode: "w1" });
    assert.ok(cells(root).length > 4);
    assert.deepEqual(offenders(root), []);
});

test("the saved-test list and comparison draw no table cell with a display-setting class", async () => {
    const window = {};
    vm.runInNewContext(readFileSync(new URL("../../app/static/js/recommendation-apply-test.js", import.meta.url), "utf8"),
        { window, setTimeout: () => 1, clearTimeout() {} });
    const run = (id, uid) => ({ runId: id, candidateId: "rec-all-workflows-a+b-combination", createdAt: "2026-10-25T05:31:06", status: "COMPLETED",
        clusterUid: uid, workflowSetDigest: "d", baselineRisk: 100, predictedRisk: 0, afterRisk: 100, namespace: "shop" });
    const root = node("section");
    const history = window.ResilienceApplyTest.createHistoryPanel({ root, document: { createElement: node }, selectedId: () => "rec-all-workflows-a+b-combination",
        fetchImpl: async () => ({ ok: true, json: async () => ({ runs: [run("r1", "u1"), run("r2", "u2")], hasMore: false }) }) });
    await history.start();
    walk(root).filter((n) => n.type === "checkbox").forEach((c) => { c.checked = true; c.listeners.change(); });
    assert.ok(cells(root).length > 30, "the list and the comparison are both drawn");
    assert.deepEqual(offenders(root), []);
});
