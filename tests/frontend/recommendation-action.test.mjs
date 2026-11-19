// Logic tests for app/static/js/recommendation-action.js: what the page offers next, for every state of the journey.
// The rule under test: one obvious action, never an enabled button the evidence does not support, and never Apply without
// a reviewed workflow and an approval.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-action.js", import.meta.url), "utf8");

function load(stored) {
    const store = new Map(stored ? [["resilience.audit.name", stored]] : []);
    const window = { localStorage: { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, String(value)) } };
    vm.runInNewContext(source, { window, console, encodeURIComponent });
    return { action: window.ResilienceAction, store };
}
const { action } = load();
const plain = (value) => JSON.parse(JSON.stringify(value));
const CHANGE = { title: "Spread replicas across nodes", target: "db" };
const ready = (over = {}) => ({ status: "ready", mode: "workflow", candidate: CHANGE, approval: "none", approvalReady: true, run: null, canApply: null, ...over });
const labels = (model) => [model.primary && model.primary.label, model.secondary && model.secondary.label];

test("without a reviewed workflow there is no Apply and no approval: only the way to establish the evidence", () => {
    for (const facts of [
        { status: "blocked", blockedBy: "workflow", mode: "deployment", candidate: CHANGE },
        { status: "blocked", blockedBy: "workflow", mode: "workflow", blockedMessage: "This workflow has no current review." },
    ]) {
        const model = plain(action.plan(facts));
        assert.equal(model.phase, "needs-workflow");
        assert.deepEqual(labels(model), ["Review a workflow", "I've reviewed one — check again"]);
        assert.ok(![model.primary.id, model.secondary.id].some((id) => ["approve", "apply", "keep", "reset"].includes(id)));
    }
});

test("other missing evidence offers a retry, or nothing when it cannot be retried", () => {
    assert.deepEqual(labels(action.plan({ status: "blocked", blockedBy: "evidence", blockedMessage: "Placement is incomplete." })), ["Check again", null]);
    assert.equal(action.plan({ status: "blocked", blockedBy: "evidence", retryable: false }).primary, null);
});

test("loading and choosing an application offer nothing", () => {
    assert.equal(action.plan({ status: "loading" }).primary, null);
    assert.equal(action.plan({ status: "needs-input" }).primary, null);
});

test("a change the server will not approve offers no approval", () => {
    const model = action.plan(ready({ approvalReady: false, approvalReason: "Not feasible on this placement." }));
    assert.equal(model.phase, "not-approvable");
    assert.match(model.text, /Not feasible/);
    assert.deepEqual(labels(model), ["Choose another change", null]);
});

test("the next step walks review, approve, apply, decide, restored in order", () => {
    assert.deepEqual(labels(action.plan(ready())), ["Review and approve", null]);
    assert.deepEqual(labels(action.plan(ready({ approval: "PENDING_APPROVAL" }))), ["Approve this change", "Reject"]);
    assert.deepEqual(labels(action.plan(ready({ approval: "REJECTED" }))), ["Choose another change", null]);
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED" }))), ["Apply to cluster", null]);
    const finished = { busy: false, finished: true, canReset: true, canKeep: true };
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED", run: finished }))), ["Reset now", "Keep changes"]);
    // Once kept, the person moves on to another change; a reset stays available while the cluster still holds the kept change.
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED", run: { kept: true, canResetKept: true } }))), ["Choose another change", "Reset change"]);
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED", run: { kept: true } }))), ["Choose another change", null]);
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED", run: { reset: true } }))), ["Apply again", null]);
});

test("a run in progress shows no usable action", () => {
    const model = action.plan(ready({ approval: "APPROVED", run: { busy: true, message: "Waiting for the rollout." } }));
    assert.equal(model.phase, "running");
    assert.equal(model.primary.disabled, true);
    assert.equal(model.secondary, null);
    assert.match(model.text, /rollout/);
});

test("a change that cannot be kept is offered only a reset, and a failed run still asks what to do with the change on the cluster", () => {
    assert.deepEqual(labels(action.plan(ready({ approval: "APPROVED", run: { finished: true, canReset: true, canKeep: false, failed: true } }))), ["Reset now", null]);
});

test("a different change that is still applied must be decided first, and is said to be a different one", () => {
    const model = action.plan(ready({ approval: "APPROVED", runIsOther: true, run: { finished: true, canReset: true, canKeep: true } }));
    assert.match(model.title, /change you applied is still on the cluster/i);
    assert.deepEqual(labels(model), ["Reset now", "Keep changes"]);
});

test("a server that says the change cannot be applied now disables Apply and gives its reason", () => {
    const model = action.plan(ready({ approval: "APPROVED", canApply: false, applyReason: "The proposal is missing." }));
    assert.equal(model.primary.disabled, true);
    assert.equal(model.primary.reason, "The proposal is missing.");
});

test("a declared workflow is research approval only: the page goes to the research form, never the production approval", () => {
    const model = action.plan(ready({ declared: true }));
    assert.equal(model.phase, "research");
    assert.deepEqual(labels(model), ["Request research approval", null]);
    assert.equal(model.primary.id, "go-approve");
    assert.equal(action.plan(ready({ declared: true, approval: "PENDING_APPROVAL" })).secondary, null);
});

test("no feasible change says it is not a finding that the application is resilient", () => {
    const model = action.plan(ready({ candidate: null }));
    assert.equal(model.phase, "no-change");
    assert.match(model.text, /not a finding that the application is resilient/);
    assert.equal(model.primary, null);
});

test("the configured name is attribution only: it is sent URL-encoded, and a blank name sends nothing", () => {
    const named = load("Amélie Perera");
    assert.equal(named.action.attribution.header()["X-Operator-Name"], encodeURIComponent("Amélie Perera"));
    assert.deepEqual(plain(load("").action.attribution.header()), {});
    const fresh = load("");
    assert.equal(fresh.action.attribution.set(" a "), false, "one character is not a name");
    assert.equal(fresh.action.attribution.set("  Sam  "), true);
    assert.equal(fresh.action.attribution.get(), "Sam");
});
