// Logic tests for namespaces.js: what the extension shows about the applications in the connected cluster.
// Everything shown comes from Member 2's GET /api/deployments/discovery/all; nothing is added or defaulted.
// Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./namespaces.js", import.meta.url), "utf8");
const window = {}; // no document: the script exposes its logic and stops before touching the DOM
vm.runInNewContext(source, { window, Math, Date, JSON, Object, Array, Promise, String, Number, Boolean, Error, Set });
const N = window.__resilienceNamespaces;
const plain = (value) => JSON.parse(JSON.stringify(value));

const service = (name, ready = 1, declared = 1) => ({ name, readyReplicas: ready, declaredReplicas: declared, pods: [], hostingNodes: [] });
const row = (namespace, services, extra = {}) => ({
    status: "ok", namespace, capturedAt: "2026-10-22T08:00:00Z", declaredState: "unavailable", warnings: [], unresolved: [],
    serviceEvidence: {}, graph: { nodes: [], services }, ...extra,
});
const all = (namespaces, extra = {}) => ({
    status: "ok", capturedAt: "2026-10-22T08:00:00Z", namespaces, excludedNamespaces: ["kube-system", "local-path-storage"],
    totals: { workerNodes: 2, services: 0, unresolved: 0 }, ...extra,
});

// ------------------------------------------------------------------ what is listed

test("every namespace with workloads is listed with the services discovery found in it, most services first", () => {
    const s = N.summarize(all([
        row("shop", [service("cart"), service("db", 3, 3)]),
        row("bookinfo", [service("productpage"), service("details"), service("reviews", 1, 2), service("ratings")]),
    ]));
    assert.deepEqual(plain(s.rows.map((r) => r.namespace)), ["bookinfo", "shop"]);
    assert.deepEqual(plain(s.rows[0].services.map((x) => x.name)), ["details", "productpage", "ratings", "reviews"]);
    assert.equal(s.rows[0].serviceCount, 4);
    assert.deepEqual(plain(s.rows[0].services[3]), { name: "reviews", ready: 1, declared: 2 });
});

test("equal service counts are ordered by name, so the list never jumps around between refreshes", () => {
    const s = N.summarize(all([row("b", [service("x")]), row("a", [service("y")])]));
    assert.deepEqual(plain(s.rows.map((r) => r.namespace)), ["a", "b"]);
});

test("nothing is invented: a namespace with no resolved service is shown as that, and unknown replica counts stay unknown", () => {
    const s = N.summarize(all([row("empty", []), row("odd", [{ name: "x" }])]));
    assert.equal(s.rows.find((r) => r.namespace === "empty").serviceCount, 0);
    assert.deepEqual(plain(s.rows.find((r) => r.namespace === "odd").services[0]), { name: "x", ready: null, declared: null });
});

test("system namespaces that discovery left out are named, never hidden silently", () => {
    const s = N.summarize(all([row("shop", [service("cart")])]));
    assert.deepEqual(plain(s.excluded), ["kube-system", "local-path-storage"]);
    assert.match(N.statusText(s).text, /kube-system/);
});

// ------------------------------------------------------------------ incomplete evidence

test("a partial namespace, or one with unresolved items, is flagged and keeps Member 2's reasons", () => {
    const s = N.summarize(all([
        row("kube-dns-ns", [service("dns", 0, 2)], { status: "partial", unresolved: [{ kind: "Pod", name: "p", reason: "POD_ON_EXCLUDED_NODE", affects: ["dns"] }] }),
        row("fine", [service("a")]),
        row("quiet", [service("b")], { serviceEvidence: { b: { complete: false, reasons: ["PODS_ON_EXCLUDED_NODES"] } } }),
    ]));
    const by = Object.fromEntries(s.rows.map((r) => [r.namespace, r]));
    assert.equal(by["kube-dns-ns"].incomplete, true);
    assert.deepEqual(plain(by["kube-dns-ns"].reasons), ["POD_ON_EXCLUDED_NODE"]);
    assert.equal(by.quiet.incomplete, true);
    assert.deepEqual(plain(by.quiet.reasons), ["PODS_ON_EXCLUDED_NODES"]);
    assert.equal(by.fine.incomplete, false);
    assert.equal(s.incomplete, 2);
});

test("either signal alone is enough to flag a namespace: a partial status, or unresolved items under an ok status", () => {
    const s = N.summarize(all([
        row("partial-only", [service("a")], { status: "partial", unresolved: [] }),
        row("unresolved-only", [service("b")], { status: "ok", unresolved: [{ reason: "POD_SELECTED_BY_NO_SERVICE" }] }),
        row("neither", [service("c")], { status: "ok", unresolved: [] }),
    ]));
    const by = Object.fromEntries(s.rows.map((r) => [r.namespace, r.incomplete]));
    assert.deepEqual(by, { "partial-only": true, "unresolved-only": true, neither: false });
});

test("incomplete evidence is explained in words and says predictions for it are refused, not estimated", () => {
    const s = N.summarize(all([row("x", [service("a")], { status: "partial", unresolved: [{ reason: "POD_ON_EXCLUDED_NODE" }] })]));
    const text = N.statusText(s).text;
    assert.match(text, /incomplete/i);
    assert.match(text, /not estimated|no result|refused/i);
    assert.match(N.optionLabel(s.rows[0]), /incomplete evidence/);
});

test("declared intent that was not available is said to be unavailable, never filled in", () => {
    const s = N.summarize(all([row("x", [service("a")], { declaredState: "unavailable" })]));
    assert.equal(s.rows[0].declaredState, "unavailable");
});

// ------------------------------------------------------------------ labels and status line

test("the label names the namespace and its service count exactly", () => {
    const s = N.summarize(all([row("shop", [service("a"), service("b")]), row("solo", [service("a")]), row("none", [])]));
    const by = Object.fromEntries(s.rows.map((r) => [r.namespace, N.optionLabel(r)]));
    assert.equal(by.shop, "shop · 2 services");
    assert.equal(by.solo, "solo · 1 service");
    assert.equal(by.none, "none · no services resolved");
});

test("the datalist value is exactly the namespace name, so choosing one fills the page's own input", () => {
    const s = N.summarize(all([row('we"ird<ns>', [service("a")])]));
    assert.deepEqual(plain(N.options(s)), [{ value: 'we"ird<ns>', label: 'we"ird<ns> · 1 service' }]);
});

test("a connected cluster with nothing running says so instead of showing an error or a made-up list", () => {
    const s = N.summarize(all([]));
    const status = N.statusText(s);
    assert.equal(status.kind, "empty");
    assert.match(status.text, /no namespace.*workload|no application/i);
    assert.deepEqual(plain(N.options(s)), []);
});

test("the status line reports the totals it was given", () => {
    const s = N.summarize(all([row("a", [service("x")]), row("b", [service("y"), service("z")])]));
    const status = N.statusText(s);
    assert.equal(status.kind, "ok");
    assert.match(status.text, /2 namespaces/);
    assert.match(status.text, /3 services/);
});

// ------------------------------------------------------------------ failures are shown as failures

test("every coded failure the API reports has its own wording", () => {
    const codes = ["CLUSTER_NOT_CONNECTED", "CLUSTER_CONNECTION_FAILED", "CLUSTER_AUTH_FAILED", "CLUSTER_FORBIDDEN", "DISCOVERY_FAILED"];
    const texts = codes.map((code) => N.describeFailure(409, { detail: { code, message: "server wording" } }).message);
    assert.equal(new Set(texts).size, codes.length);
    for (const t of texts) assert.doesNotMatch(t, /server wording/);
});

test("not connected points at the Connect page and is not an error about the cluster", () => {
    const f = N.describeFailure(503, { detail: { code: "CLUSTER_NOT_CONNECTED" } });
    assert.equal(f.state, "not-connected");
    assert.match(f.message, /connect/i);
});

test("an expired or rejected credential says so", () => {
    assert.match(N.describeFailure(401, { detail: { code: "CLUSTER_AUTH_FAILED" } }).message, /credential/i);
});

test("no answer, an unknown code and a malformed body are each reported, never treated as an empty cluster", () => {
    assert.equal(N.describeFailure(0, null).state, "backend-unavailable");
    assert.match(N.describeFailure(500, { detail: { code: "SOMETHING_NEW" } }).message, /SOMETHING_NEW/);
    assert.equal(N.describeFailure(200, { unexpected: true }).state, "unexpected-response");
    assert.throws(() => N.summarize({ unexpected: true }), /unexpected/i);
    assert.throws(() => N.summarize({ namespaces: "nope" }), /unexpected/i);
});

test("a hostile error CODE is not shown either: only well-formed codes are named", () => {
    const f = N.describeFailure(500, { detail: { code: "<img src=x onerror=alert(1)>" } });
    assert.doesNotMatch(f.message, /img|onerror|</);
    assert.match(f.message, /HTTP 500/);
});

test("hostile text in a failure's message is never shown", () => {
    const f = N.describeFailure(500, { detail: { code: "DISCOVERY_FAILED", message: "<img src=x onerror=alert(1)>" } });
    assert.doesNotMatch(f.message, /img|onerror/);
});

// ------------------------------------------------------------------ refreshing while visible

function world({ responses, visible = true }) {
    const timers = [];
    const requests = [];
    const updates = [];
    const state = { visible, ms: 1_000_000, next: 0 };
    const env = {
        fetchJson: async (url) => {
            requests.push(url);
            const r = responses[Math.min(requests.length - 1, responses.length - 1)];
            return typeof r === "function" ? r() : r;
        },
        now: () => state.ms,
        isVisible: () => state.visible,
        // A timer that has fired is no longer pending, exactly as in a browser.
        setTimer: (fn, ms) => { const t = { id: ++state.next, ms, live: true }; t.fn = async () => { t.live = false; return fn(); }; timers.push(t); return t.id; },
        clearTimer: (id) => { const t = timers.find((x) => x.id === id); if (t) t.live = false; },
        onUpdate: (snapshot) => updates.push(plain(snapshot)),
    };
    return { env, timers, requests, updates, state, refresher: N.createRefresher(env), live: () => timers.filter((t) => t.live) };
}
const okResponse = (...rows) => ({ status: 200, body: all(rows) });
const failure = (code, status = 503) => ({ status, body: { detail: { code } } });

test("starting reads the cluster at once, only through Member 2's discovery API, and schedules the next read", async () => {
    const w = world({ responses: [okResponse(row("shop", [service("a")]))] });
    await w.refresher.start();
    assert.deepEqual(w.requests, ["/api/deployments/discovery/all"]);
    assert.equal(w.refresher.snapshot().state, "ok");
    assert.equal(w.live().length, 1);
    assert.equal(w.live()[0].ms, N.REFRESH_MS);
});

test("it refreshes every minute while visible, and reads nothing while the tab is hidden", async () => {
    const w = world({ responses: [okResponse(row("shop", [service("a")]))] });
    await w.refresher.start();
    w.state.visible = false;
    await w.live()[0].fn();
    assert.equal(w.requests.length, 1, "a hidden tab must not read the cluster");
    assert.equal(w.live().length, 1, "but it keeps watching, so it resumes when shown");
    w.state.visible = true;
    await w.live()[0].fn();
    assert.equal(w.requests.length, 2);
});

test("two refreshes at once share one request", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const w = world({ responses: [async () => { await gate; return okResponse(); }] });
    const a = w.refresher.refresh();
    const b = w.refresher.refresh();
    release();
    await Promise.all([a, b]);
    assert.equal(w.requests.length, 1);
});

test("a failure replaces the list at once, so names from an earlier cluster are never offered as if current", async () => {
    const w = world({ responses: [okResponse(row("shop", [service("a")])), failure("CLUSTER_CONNECTION_FAILED")] });
    await w.refresher.start();
    assert.equal(w.refresher.snapshot().summary.rows.length, 1);
    await w.refresher.refresh();
    const s = w.refresher.snapshot();
    assert.equal(s.state, "error");
    assert.equal(s.summary, null);
    assert.equal(s.failure.state, "connection-failed");
});

test("failures are retried with growing delays and success returns to the normal rhythm", async () => {
    const w = world({ responses: [failure("CLUSTER_CONNECTION_FAILED"), failure("CLUSTER_CONNECTION_FAILED"), failure("CLUSTER_CONNECTION_FAILED"),
        failure("CLUSTER_CONNECTION_FAILED"), okResponse(row("shop", [service("a")]))] });
    const delays = [];
    await w.refresher.start();
    delays.push(w.live()[0].ms);
    for (let i = 0; i < 3; i++) { await w.live()[0].fn(); delays.push(w.live()[0].ms); }
    assert.deepEqual(delays, [15000, 30000, 60000, 60000], "growing, capped at a minute");
    await w.live()[0].fn();
    assert.equal(w.live()[0].ms, N.REFRESH_MS);
    assert.equal(w.refresher.snapshot().state, "ok");
});

test("after a failure, a change of connection (reconnected, renewed, set up) is read again without waiting", async () => {
    const w = world({ responses: [failure("CLUSTER_NOT_CONNECTED"), okResponse(row("shop", [service("a")]))] });
    await w.refresher.start();
    assert.equal(w.refresher.snapshot().failure.state, "not-connected");
    w.state.ms += 1_000; // well inside the freshness window
    await w.refresher.connectionChanged();
    assert.equal(w.refresher.snapshot().state, "ok");
    assert.equal(w.live().length, 1, "never more than one timer pending");
});

test("while the list is good, the connection announcing itself every minute does not double the reads", async () => {
    const w = world({ responses: [okResponse(row("shop", [service("a")]))] });
    await w.refresher.start();
    for (let i = 0; i < 5; i++) { w.state.ms += 1_000; await w.refresher.connectionChanged(); }
    assert.equal(w.requests.length, 1);
    w.state.ms += 30_000;
    await w.refresher.connectionChanged();
    assert.equal(w.requests.length, 2, "but a stale list is read again");
});

test("coming back to the tab refreshes only when the last read is older than fifteen seconds", async () => {
    const w = world({ responses: [okResponse(row("shop", [service("a")]))] });
    await w.refresher.start();
    w.state.ms += 5_000;
    await w.refresher.refreshIfStale();
    assert.equal(w.requests.length, 1);
    w.state.ms += 20_000;
    await w.refresher.refreshIfStale();
    assert.equal(w.requests.length, 2);
});

test("stopping cancels the timer", async () => {
    const w = world({ responses: [okResponse()] });
    await w.refresher.start();
    w.refresher.stop();
    assert.equal(w.live().length, 0);
});

test("a read that never answers cannot block the next one forever", async () => {
    const w = world({ responses: [() => new Promise(() => {}), okResponse(row("shop", [service("a")]))] });
    w.env.sleep = (ms) => new Promise((resolve) => setTimeout(resolve, Math.min(ms, 5)));
    const r2 = N.createRefresher({ ...w.env, requestTimeoutMs: 1 });
    await r2.refresh();
    assert.equal(r2.snapshot().failure.state, "backend-unavailable");
    await r2.refresh();
    assert.equal(r2.snapshot().state, "ok");
});

test("the extension only ever reads: the one URL it requests is a GET of discovery/all", () => {
    assert.equal(N.DISCOVERY_URL, "/api/deployments/discovery/all");
    assert.doesNotMatch(source, /method\s*:\s*["'](?:POST|PUT|PATCH|DELETE)/i);
});
