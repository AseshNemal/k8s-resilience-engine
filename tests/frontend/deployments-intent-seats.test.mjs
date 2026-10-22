// Declared vs live tab: one seat per declared copy, filled by running pods.
// Run with: node --test tests/frontend/deployments-intent-seats.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_intent_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console });
const { planSeats } = window.DeploymentsIntentWidget;

const pod = (node, ready = true) => ({ name: `p-${node}`, node, ready });
const kinds = (seats) => [...seats.map((s) => s.kind)];  // copied out of the vm context

test("every declared copy filled by a ready pod is a match", () => {
    assert.deepEqual(kinds(planSeats(3, [pod("w1"), pod("w2"), pod("w3")])), ["ok", "ok", "ok"]);
});

test("declared copies with no pod are missing", () => {
    assert.deepEqual(kinds(planSeats(3, [pod("w1")])), ["ok", "missing", "missing"]);
});

test("a pod that is not ready fills its seat but is shown as not ready, after the ready ones", () => {
    assert.deepEqual(kinds(planSeats(2, [pod("w1", false), pod("w2")])), ["ok", "notready"]);
});

test("pods beyond what the YAML asks for are extra", () => {
    assert.deepEqual(kinds(planSeats(1, [pod("w1"), pod("w2")])), ["ok", "extra"]);
});
