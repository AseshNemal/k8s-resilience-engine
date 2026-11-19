// The shared connection state: what a saved record is called, and that nothing is ever called live without the cluster's own answer.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SCRIPT = readFileSync(new URL("../../app/static/js/cluster-state.js", import.meta.url), "utf8");

function load() {
    const sandbox = { window: {}, console, Date, Math, JSON, Number, String, Object, Array, Set, Promise };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "cluster-state.js" });
    return sandbox.window.ResilienceCluster;
}
const plain = (v) => JSON.parse(JSON.stringify(v));
const NOW = Date.parse("2026-11-19T12:00:00Z");
const LIVE = { state: "connected", clusterUid: "uid-1" };

test("with no cluster, a saved record says it is saved and that nothing is being measured now", () => {
    const c = load();
    for (const state of ["not-connected", "unreachable", "access-denied", "backend-unreachable", "checking"]) {
        const d = c.describeSaved({ state }, { clusterUid: "uid-1", at: "2026-11-19T11:00:00Z" }, NOW);
        assert.equal(d.kind, "saved-offline");
        assert.match(d.label, /^Saved result · 60 min ago$/);
        assert.match(d.detail, /not live/);
    }
});

test("a record from another cluster is never presented as about the connected one", () => {
    const c = load();
    const d = c.describeSaved(LIVE, { clusterUid: "uid-2", at: "2026-11-19T11:59:00Z", clusterName: "kind-x" }, NOW);
    assert.equal(d.kind, "saved-other");
    assert.match(d.label, /different cluster/);
    assert.match(d.detail, /says nothing about the cluster connected now/);
});

test("a record from the same cluster is still saved, with a note that the cluster may have changed", () => {
    const c = load();
    const d = c.describeSaved(LIVE, { clusterUid: "uid-1", at: "2026-11-17T12:00:00Z" }, NOW);
    assert.equal(d.kind, "saved-same");
    assert.match(d.label, /2 days ago/);
    assert.match(d.detail, /may have changed/);
});

test("a record that never stored its cluster is labelled unknown, not matched", () => {
    const c = load();
    assert.equal(c.describeSaved(LIVE, { at: "2026-11-19T11:59:30Z" }, NOW).kind, "saved-unknown");
});

test("only the state 'connected' counts as live", () => {
    const c = load();
    assert.equal(c._isLive({ state: "connected" }), true);
    for (const state of ["checking", "not-connected", "unreachable", "access-denied", "unidentified", "backend-unreachable", undefined]) {
        assert.equal(c._isLive({ state }), false, String(state));
    }
    assert.equal(c.isLive(), false);       // before any answer, nothing is live
});

test("every state has words; an unknown state is treated as the backend not answering, never as connected", () => {
    const c = load();
    assert.equal(c.words("mystery").chip, c.words("backend-unreachable").chip);
    assert.match(c.words("not-connected").title, /Connect your cluster/);
});

test("ages are worded in the person's terms", () => {
    const c = load();
    assert.equal(c.ago("2026-11-19T11:59:30Z", NOW), "just now");
    assert.equal(c.ago("2026-11-19T11:30:00Z", NOW), "30 min ago");
    assert.equal(c.ago("2026-11-18T12:00:00Z", NOW), "24 h ago");
    assert.equal(c.ago("nonsense", NOW), null);
});

test("there is no Connect link where the app has no Connect page", () => {
    const c = load();
    assert.equal(c.connectHref(), null);
});
