// Logic tests for app/static/js/failure-impact-view.js. Run with: node --test tests/frontend/
// The view only labels what the server computed; an unknown figure is never drawn as zero.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/failure-impact-view.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, console });
const view = window.ResilienceFailureView;
const plain = (value) => JSON.parse(JSON.stringify(value));

const node = (overrides = {}) => ({
    node: "w1", verdict: "workflow-lost", servicesDown: ["db"], servicesDegraded: ["web"],
    workflowsEvaluated: 3, workflowsLost: 1, blastRadius: 100 / 3, ...overrides,
});

test("replica dots mark the replicas on the failed node as lost and cap the row", () => {
    assert.deepEqual(plain(view.replicaDots({ readyBefore: 3, lostReady: 1 })), { dots: ["lost", "kept", "kept"], hidden: 0 });
    assert.deepEqual(plain(view.replicaDots({ readyBefore: 1, lostReady: 1 })), { dots: ["lost"], hidden: 0 });
    const many = view.replicaDots({ readyBefore: 12, lostReady: 12 });
    assert.equal(many.dots.length, view.MAX_DOTS);
    assert.equal(many.hidden, 4);
    assert.deepEqual(plain(view.replicaDots({ readyBefore: 0, lostReady: 0 })), { dots: [], hidden: 0 });
    assert.deepEqual(plain(view.replicaDots({ readyBefore: 2, lostReady: 9 })).dots, ["lost", "lost"], "never more lost than were ready");
});

test("without a workflow the workflow figures are unknown, not zero", () => {
    const stats = plain(view.nodeStats(node({ workflowsEvaluated: 0, workflowsLost: null, blastRadius: null })));
    assert.deepEqual(stats.map((s) => [s.label, s.value, s.tone]), [
        ["Services down", "1", "danger"], ["Workflows stopped", "unknown", "unknown"], ["Importance lost", "unknown", "unknown"]]);
});

test("with workflows the figures are the server's, and zero stays zero", () => {
    const stats = plain(view.nodeStats(node({ servicesDown: [], workflowsLost: 0, blastRadius: 0 })));
    assert.deepEqual(stats.map((s) => [s.value, s.tone]), [["0", "ok"], ["0 of 3", "ok"], ["0%", "ok"]]);
    assert.equal(view.nodeStats(node())[2].value, "33%", "falls back to the plain share when no weighted figure was reported");
    assert.equal(view.nodeStats(node({ weightedBlastRadius: 78.18 }))[2].value, "78%", "the weighted figure Recommendations shows for the same failure");
});

test("the one-line summary names what goes down, then workflows, then reduced replicas, then nothing", () => {
    assert.equal(view.cardLine(node()), "db down · 1 workflow stop");
    assert.equal(view.cardLine(node({ servicesDown: [], workflowsLost: 0 })), "1 service with fewer replicas");
    assert.equal(view.cardLine(node({ servicesDown: [], servicesDegraded: [], workflowsLost: 0, verdict: "no-impact" })), "Nothing goes down");
    assert.equal(view.cardLine(node({ servicesDown: [], servicesDegraded: [], workflowsLost: 0, verdict: "no-pods" })), "Hosts no ready pods here");
    assert.equal(view.cardLine(node({ workflowsLost: 2 })), "db down · 2 workflows stop");
});

test("an unknown verdict or outcome is drawn as unknown, never as healthy", () => {
    assert.equal(view.verdict("something-new").text, "Unknown");
    assert.equal(view.serviceOutcome("something-new").variant, "muted");
    assert.equal(view.verdict("no-impact").variant, "success");
});

test("the basis badge says declared workflows are not observed", () => {
    assert.match(view.basisBadge({ kind: "declared" }).text, /not observed/);
    assert.equal(view.basisBadge({ kind: "frozen" }).variant, "success");
    assert.match(view.basisBadge({ kind: "none" }).text, /service level only/);
    assert.match(view.basisBadge(undefined).text, /service level only/);
});

test("a refresh keeps the chosen node if it is still there, else the worst", () => {
    const nodes = [{ node: "a" }, { node: "b" }];
    assert.equal(view.chooseNode(nodes, "b"), "b");
    assert.equal(view.chooseNode(nodes, "gone"), "a");
    assert.equal(view.chooseNode(nodes, null), "a");
    assert.equal(view.chooseNode([], "a"), null);
});

test("workflow lines say why a workflow stops, and mention an unavailable optional service", () => {
    assert.match(view.workflowLine({ survives: false, failedMandatoryServices: ["db", "web"], unavailableOptionalServices: [] }), /db, web would have no ready replica/);
    assert.match(view.workflowLine({ survives: true, failedMandatoryServices: [], unavailableOptionalServices: ["ratings"] }), /Optional ratings/);
    assert.match(view.workflowLine({ survives: true, failedMandatoryServices: [], unavailableOptionalServices: [] }), /keeps a ready replica/);
});

test("the hand-off is bound to the cluster fingerprint, expires, and refuses an empty one", () => {
    const record = view.handoffRecord("bookinfo", "desktop-worker", "abc123", 1000);
    assert.deepEqual(plain(record), { namespace: "bookinfo", failedNode: "desktop-worker", fingerprint: "abc123", at: 1000 });
    assert.equal(view.handoffIsFresh(record, 1000 + 60_000), true);
    assert.equal(view.handoffIsFresh(record, 1000 + 11 * 60_000), false);
    assert.equal(view.handoffIsFresh(view.handoffRecord("bookinfo", "w", "", 1000), 1001), false, "no fingerprint, no hand-off");
    assert.equal(view.handoffIsFresh(view.handoffRecord("bookinfo", "", "abc", 1000), 1001), false);
    assert.equal(view.handoffIsFresh(null, 1), false);
});

test("a workflow is shown by its own name, with the identifier only as a fallback", () => {
    assert.equal(view.workflowLabel({ workflowId: "9e28bd30", name: "verify-manual-productpage" }), "verify-manual-productpage");
    assert.equal(view.workflowLabel({ workflowId: "productpage-view", name: null }), "productpage-view");
    assert.equal(view.workflowLabel({ workflowId: "x", name: "  " }), "x");
});
