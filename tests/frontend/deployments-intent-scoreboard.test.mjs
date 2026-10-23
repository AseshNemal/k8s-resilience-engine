// Declared vs live tab: the scoreboard's verdict, YAML copies against ready copies.
// Run with: node --test tests/frontend/deployments-intent-scoreboard.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_intent_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console });
const { compareCounts } = window.DeploymentsIntentWidget;
const plain = (v) => ({ ...v });  // copied out of the vm context

test("the same number of ready copies as the YAML is a match", () => {
    assert.deepEqual(plain(compareCounts(3, 3)), { tone: "ok", diff: 0, text: "✓ Same as the YAML" });
});

test("more ready copies than the YAML (scaled by hand) are shown as more", () => {
    assert.deepEqual(plain(compareCounts(1, 3)), { tone: "warn", diff: 2, text: "➕ 2 more than the YAML" });
});

test("fewer ready copies than the YAML are shown as fewer", () => {
    assert.deepEqual(plain(compareCounts(3, 1)), { tone: "danger", diff: -2, text: "⚠️ 2 fewer than the YAML" });
});

test("a service the cluster does not run is not compared", () => {
    assert.equal(compareCounts(2, null).tone, "muted");
    assert.equal(compareCounts(null, 2).diff, null);
});
