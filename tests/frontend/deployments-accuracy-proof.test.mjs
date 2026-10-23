// Accuracy tab: the checks of each real node failure, grouped for the "model predicted vs Kubernetes showed" view.
// Run with: node --test tests/frontend/deployments-accuracy-proof.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_academic_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console });
const { failureCases } = window.DeploymentsAcademicWidget;

const check = (node, service, predicted, observed) => ({
    failed_nodes: [node], service, predicted_available: predicted, observed_available: observed,
});

test("checks are grouped per failed node, nodes and services in order", () => {
    const cases = failureCases([
        check("w2", "ratings", false, false), check("w1", "reviews", false, false),
        check("w2", "details", false, false), check("w1", "details", true, true),
    ]);
    assert.deepEqual([...cases.map((c) => c.node)], ["w1", "w2"]);
    assert.deepEqual([...cases[1].checks.map((c) => c.service)], ["details", "ratings"]);
});

test("no checks give no cases", () => {
    assert.equal(failureCases([]).length, 0);
    assert.equal(failureCases(undefined).length, 0);
});
