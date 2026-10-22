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
