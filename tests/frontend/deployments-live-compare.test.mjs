// Live Experiments tab: "Same outcome?" between the stand-in and Bookinfo runs of one condition.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_live_experiments_widget.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(script, { window, document: {}, console, encodeURIComponent, Math, String, Object, JSON });
const { sameOutcome } = window.DeploymentsLiveExperimentsWidget;

const cond = (statuses, unstable, dRate) => ({
    target_status_t1_counts: statuses, unstable_runs: unstable, d_rate: { mean: dRate }, runs: 5, failed_runs: 0,
});

test("the same target status, settling and D_rate is the same outcome", () => {
    assert.equal(sameOutcome(cond({ EXPOSED: 5 }, 5, 0.25), cond({ EXPOSED: 5 }, 5, 0.25)), true);
});

test("a different target status is a different outcome", () => {
    assert.equal(sameOutcome(cond({ EXPOSED: 5 }, 0, 0.25), cond({ NOT_EXPOSED: 5 }, 0, 0.25)), false);
});

test("pods left unsettled in only one workload is a different outcome", () => {
    assert.equal(sameOutcome(cond({ NOT_EXPOSED: 5 }, 5, 0), cond({ NOT_EXPOSED: 5 }, 0, 0)), false);
});

test("a different mean D_rate is a different outcome", () => {
    assert.equal(sameOutcome(cond({ EXPOSED: 3, NOT_EXPOSED: 2 }, 0, 0.15), cond({ EXPOSED: 3, NOT_EXPOSED: 2 }, 0, 0.25)), false);
});

test("every key finding has a short title and a generated sentence", () => {
    const { buildFindings } = window.DeploymentsLiveExperimentsWidget;
    const stat = (mean) => ({ mean, sd: 0 });
    const condition = (id, config, exposed, unstable) => ({
        scenario_id: id, scenario_name: id, config, runs: 5, failed_runs: 0, unstable_runs: unstable,
        target_became_exposed: exposed ? 5 : 0, d_rate: stat(exposed ? 0.25 : 0),
        compliance_rate_t1: stat(config === "B-spread" ? 1 : null),
    });
    const findings = buildFindings({
        target_service: "reviews",
        conditions: [condition("08", "A-no-rule", true, 0), condition("08", "B-spread", true, 5), condition("02", "B-spread", false, 5)],
    });
    const titles = findings.map((f) => f.title);
    assert.ok(titles.includes("Exposed while Kubernetes reported it healthy"));
    assert.ok(titles.includes("Rule obeyed, service still exposed"));
    assert.ok(titles.includes("The spread rule left pods Pending"));
    for (const f of findings) {
        assert.ok(f.title && f.text, JSON.stringify(f));
    }
});
