// Logic tests for tracing-panel.js: the wording of what is true, which buttons make sense, when the panel brings
// the person's own trace service back, and that it only ever reads and writes guidance text. Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (name) => readFileSync(new URL(`./${name}`, import.meta.url), "utf8");
const window = {}; // no document: the scripts expose their logic and stop before touching the DOM
const context = { window, Set, Object, Promise, Boolean, Error, JSON, String, Number, Date, Math, Array, URLSearchParams, Infinity };
vm.runInNewContext(read("trace-service.js"), context);
vm.runInNewContext(read("tracing-panel.js"), context);
const P = window.__resilienceTracingPanel;
const plain = (value) => JSON.parse(JSON.stringify(value));

const NOW = Date.parse("2026-10-22T12:00:00Z");
const iso = (secondsAgo) => new Date(NOW - secondsAgo * 1000).toISOString();
const backend = (state, extra = {}) => ({
    query: { url: "http://x:16686", reachable: state !== "UNREACHABLE" },
    telemetry: { state, serviceCount: 2, services: [], newestTraceAt: iso(30), freshSeconds: 300, ...extra },
});
const helper = (state, enabled = false) => ({ ok: true, state, trace: { enabled, endpoints: [] } });

test("relative times read naturally and refuse nonsense", () => {
    assert.equal(P.ago(iso(5), NOW), "5 s ago");
    assert.equal(P.ago(iso(150), NOW), "2 min ago");
    assert.equal(P.ago(iso(7200), NOW), "2 h ago");
    assert.equal(P.ago(iso(3 * 86400), NOW), "3 d ago");
    assert.equal(P.ago("not a time", NOW), "");
    assert.equal(P.ago(iso(-100), NOW), "0 s ago"); // a clock skew never shows a negative time
});

test("running and receiving says so, and names where the service came from", () => {
    const v = P.describe({ helper: helper("trace-running", true), backend: backend("RECEIVING") }, NOW);
    assert.equal(v.kind, "ok");
    assert.match(v.headline, /started by the extension/);
    assert.match(v.detail, /Receiving real traces from 2 services\. Newest began 30 s ago/);
    assert.equal(v.reachable, true);
});

test("a trace service someone else started is described as theirs", () => {
    const v = P.describe({ helper: helper("trace-running-external"), backend: backend("RECEIVING") }, NOW);
    assert.match(v.headline, /started somewhere else; the extension leaves it as it is/);
});

test("waiting for the first trace says nothing was sent and the extension never sends one", () => {
    const v = P.describe({ helper: helper("trace-running", true), backend: backend("NO_SERVICES", { serviceCount: 0, newestTraceAt: null }) }, NOW);
    assert.equal(v.kind, "idle");
    assert.match(v.detail, /Waiting for the first trace/);
    assert.match(v.detail, /never sends a trace of its own/);
});

test("stale telemetry is a warning with the window in minutes", () => {
    const v = P.describe({ helper: helper("trace-running"), backend: backend("STALE", { serviceCount: 1, freshSeconds: 600 }) }, NOW);
    assert.equal(v.kind, "warn");
    assert.match(v.detail, /1 service reported before, but no trace began in the last 10 minutes/);
});

test("an unreachable service reports nothing about telemetry, and a missing backend is a warning", () => {
    const down = P.describe({ helper: helper("trace-stopped"), backend: backend("UNREACHABLE") }, NOW);
    assert.equal(down.reachable, false);
    assert.match(down.detail, /nothing is known about incoming traces/);
    assert.equal(down.headline, "The trace service is stopped.");

    const lost = P.describe({ helper: helper("trace-running"), backend: null }, NOW);
    assert.equal(lost.kind, "warn");
    assert.match(lost.headline, /backend did not answer/);

    const docker = P.describe({ helper: helper("docker-unavailable"), backend: backend("UNREACHABLE") }, NOW);
    assert.equal(docker.kind, "error");
});

test("buttons: enable only before it is turned on, never for someone else's service, never without the helper", () => {
    assert.deepEqual(plain(P.actions(helper("trace-not-created"))), { enable: true, turnOff: false, check: true });
    assert.deepEqual(plain(P.actions(helper("trace-running", true))), { enable: false, turnOff: true, check: true });
    assert.deepEqual(plain(P.actions(helper("trace-stopped", true))), { enable: false, turnOff: true, check: true });
    assert.deepEqual(plain(P.actions(helper("trace-running-external"))), { enable: false, turnOff: false, check: true });
    assert.deepEqual(plain(P.actions(helper("docker-unavailable"))), { enable: false, turnOff: false, check: false });
    assert.deepEqual(plain(P.actions({ state: "helper-unavailable" })), { enable: false, turnOff: false, check: false });
    assert.deepEqual(plain(P.actions(null)), { enable: false, turnOff: false, check: false });
});

test("an address that was tested says whether it answered; one that was not says so", () => {
    const ok = P.describeEndpoint({ name: "otlp-http", address: "127.0.0.1:4318", reachable: true, evidence: "host" });
    assert.equal(ok.state, "ok");
    assert.match(ok.text, /answers · 127\.0\.0\.1:4318/);
    assert.equal(P.describeEndpoint({ name: "query", address: "", reachable: false, evidence: "host" }).state, "fail");
    assert.equal(P.describeEndpoint({ name: "otlp-grpc", address: "", reachable: null, evidence: "host" }).state, "unknown");
});

test("a node reaching the receiver is never described as a Pod reaching it", () => {
    const pods = (reachable) => P.describeEndpoint({ name: "pods-otlp-http", address: "host.docker.internal:4318", reachable, evidence: "node" });
    assert.match(pods(true).text, /Every node reached it; a Pod itself has not been tested/);
    assert.match(pods(false).text, /cannot send traces here yet/);
    assert.match(pods(null).text, /Could not be tested here/);
    assert.equal(pods(null).state, "unknown");
});

test("an application's services are summarised with what is missing", () => {
    const app = {
        namespace: "bookinfo", producing: 1, total: 3, freshSeconds: 300,
        services: [
            { service: "details", state: "NOT_SEEN", lastTraceAt: null },
            { service: "productpage", state: "PRODUCING", lastTraceAt: iso(12) },
            { service: "reviews", state: "STALE", lastTraceAt: iso(4000) },
        ],
    };
    const d = P.describeApplication(app, NOW);
    assert.equal(d.summary, "bookinfo: 1 of 3 services producing traces");
    assert.deepEqual(plain(d.missing), ["details", "reviews"]);
    assert.equal(d.allProducing, false);
    assert.match(d.lines[0], /details: no traces received under this name/);
    assert.match(d.lines[1], /productpage: producing traces \(newest began 12 s ago\)/);
    assert.match(d.lines[2], /reviews: known to the trace service, but no trace began in the last 5 minutes/);
});

test("guidance is text only and does not tell the person the cluster can reach the receiver unless it was tested", () => {
    const base = { namespace: "bookinfo", services: ["details", "reviews"] };
    const good = P.setupGuidance({ ...base, pods: { address: "host.docker.internal:4318", reachable: true } });
    const bad = P.setupGuidance({ ...base, pods: { address: "host.docker.internal:4318", reachable: false } });
    const untested = P.setupGuidance({ ...base, pods: null });

    assert.match(good.lines.join("\n"), /A Pod has not been tested/);
    assert.match(bad.lines.join("\n"), /could NOT reach.*do not apply this yet/s);
    assert.match(untested.lines.join("\n"), /has not been tested\. Use "Check connections" first/);
    for (const g of [good, bad, untested]) {
        assert.match(g.snippet, /^OTEL_EXPORTER_OTLP_ENDPOINT=http:\/\/host\.docker\.internal:4318$/m);
        assert.match(g.lines.join("\n"), /does not change your Deployments/);
        assert.match(g.lines.join("\n"), /Variables only configure an SDK that is already there/);
        assert.match(g.lines.join("\n"), /Bookinfo's applications forward trace headers but export no spans themselves/);
        assert.doesNotMatch(g.snippet + g.lines.join("\n"), /kubectl|istioctl|helm /);
    }
});

// ---- the controller --------------------------------------------------------------------------------------------

function harness({ helperAnswers, backendBody = backend("RECEIVING"), namespaces = null, now = { t: NOW } } = {}) {
    const calls = [];
    const queue = [...(helperAnswers || [])];
    const answer = (name) => async () => {
        calls.push(name);
        const next = queue.length > 1 ? queue.shift() : queue[0];
        return typeof next === "function" ? next(name) : next;
    };
    const urls = [];
    const panel = P.createPanel({
        traceService: {
            available: true,
            status: answer("status"), ensure: answer("ensure"), enable: answer("enable"), stop: answer("stop"), validate: answer("validate"),
        },
        fetchJson: async (url) => { urls.push(url); return backendBody === null ? { status: 0, body: null } : { status: 200, body: typeof backendBody === "function" ? backendBody(url) : backendBody }; },
        namespaces: () => namespaces,
        now: () => now.t,
    });
    return { panel, calls, urls, now };
}

test("a service that is turned on but not running is brought back, without asking anyone", async () => {
    const h = harness({ helperAnswers: [helper("trace-stopped", true)] });
    await h.panel.refresh();
    assert.deepEqual(h.calls, ["status", "ensure"]);
});

test("the panel does not try to bring back what is running, external, not turned on, or without Docker", async () => {
    for (const answer of [helper("trace-running", true), helper("trace-running-external", true), helper("trace-stopped", false), helper("docker-unavailable", true)]) {
        const h = harness({ helperAnswers: [answer] });
        await h.panel.refresh();
        assert.deepEqual(h.calls, ["status"], answer.state);
    }
});

test("recovery is not retried on every poll", async () => {
    const h = harness({ helperAnswers: [helper("trace-stopped", true)] });
    await h.panel.refresh();
    await h.panel.refresh();
    assert.deepEqual(h.calls, ["status", "ensure", "status"]);
    h.now.t += P.ENSURE_COOLDOWN_MS + 1;
    await h.panel.refresh();
    assert.equal(h.calls.filter((c) => c === "ensure").length, 2);
});

test("turning tracing on asks the helper, then refreshes and tests the addresses", async () => {
    const h = harness({ helperAnswers: [(name) => name === "enable" ? { ok: true, state: "trace-running", message: "ok", trace: { enabled: true, endpoints: [] } } : name === "validate" ? { ok: true, state: "trace-endpoints-ok", trace: { enabled: true, endpoints: [{ name: "query", address: "", reachable: true, evidence: "host", note: "" }] } } : helper("trace-running", true)] });
    const v = await h.panel.enable();
    assert.deepEqual(h.calls.slice(0, 2), ["enable", "status"]);
    assert.ok(h.calls.includes("validate"));
    assert.equal(v.endpoints.length, 1);
    assert.equal(v.busy, "");
});

test("a declined or unanswered dialog tests nothing and shows the helper's fixed wording", async () => {
    const declined = { ok: false, state: "declined", message: "You declined, so nothing was started.", trace: { enabled: false, endpoints: [] } };
    const h = harness({ helperAnswers: [(name) => name === "enable" ? declined : helper("trace-not-created")] });
    const v = await h.panel.enable();
    assert.equal(h.calls.includes("validate"), false);
    assert.equal(v.message, "You declined, so nothing was started.");
});

test("turning tracing off stops it and clears the address checks", async () => {
    const h = harness({ helperAnswers: [(name) => name === "stop" ? { ok: true, state: "trace-stopped", message: "The trace service is stopped.", trace: { enabled: false, endpoints: [] } } : helper("trace-stopped", false)] });
    const v = await h.panel.turnOff();
    assert.ok(h.calls.includes("stop"));
    assert.equal(v.message, "The trace service is stopped.");
    assert.deepEqual(plain(v.endpoints), []);
});

test("applications are checked per namespace with the services the cluster reported", async () => {
    const namespaces = { state: "ok", summary: { rows: [
        { namespace: "bookinfo", services: [{ name: "productpage" }, { name: "details" }] },
        { namespace: "empty", services: [] },
    ] } };
    const h = harness({
        helperAnswers: [helper("trace-running", true)], namespaces,
        backendBody: () => ({ namespace: "bookinfo", producing: 1, total: 2, freshSeconds: 300, services: [{ service: "details", state: "NOT_SEEN" }, { service: "productpage", state: "PRODUCING", lastTraceAt: iso(3) }] }),
    });
    const v = await h.panel.applications();
    assert.deepEqual(h.urls, ["/_extension/tracing/applications?namespace=bookinfo&service=productpage&service=details"]);
    assert.equal(v.apps.length, 1);
    assert.equal(v.apps[0].summary, "bookinfo: 1 of 2 services producing traces");
});

test("at most five namespaces are checked, and a cluster that is not listed asks nothing", async () => {
    const rows = Array.from({ length: 9 }, (_v, i) => ({ namespace: `ns${i}`, services: [{ name: "a" }] }));
    const h = harness({ helperAnswers: [helper("trace-running")], namespaces: { state: "ok", summary: { rows } }, backendBody: () => null });
    await h.panel.applications();
    assert.equal(h.urls.length, 5);

    const none = harness({ helperAnswers: [helper("trace-running")], namespaces: { state: "error", summary: null } });
    await none.panel.applications();
    assert.deepEqual(none.urls, []);
});

test("guidance is offered only for a namespace that has services without traces", async () => {
    const namespaces = { state: "ok", summary: { rows: [{ namespace: "bookinfo", services: [{ name: "a" }, { name: "b" }] }] } };
    const body = (states) => () => ({ namespace: "bookinfo", producing: 0, total: 2, freshSeconds: 300, services: [{ service: "a", state: states[0] }, { service: "b", state: states[1] }] });

    const some = harness({ helperAnswers: [helper("trace-running")], namespaces, backendBody: body(["PRODUCING", "NOT_SEEN"]) });
    await some.panel.applications();
    assert.match(some.panel.guidanceFor("bookinfo").lines[0], /^b in "bookinfo" have not sent/);

    const all = harness({ helperAnswers: [helper("trace-running")], namespaces, backendBody: body(["PRODUCING", "PRODUCING"]) });
    await all.panel.applications();
    assert.equal(all.panel.guidanceFor("bookinfo"), null);
    assert.equal(all.panel.guidanceFor("other"), null);
});

test("with no host helper the card still shows telemetry but offers nothing to start", async () => {
    const panel = P.createPanel({ traceService: { available: false }, fetchJson: async () => ({ status: 200, body: backend("RECEIVING") }), namespaces: () => null, now: () => NOW });
    const v = await panel.refresh();
    assert.equal(v.kind, "ok");
    assert.deepEqual(plain(v.actions), { enable: false, turnOff: false, check: false });
});

test("the script reads and shows text only: no HTML writing, no logging, no request that changes anything", () => {
    const code = read("tracing-panel.js").replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
    assert.doesNotMatch(code, /innerHTML|insertAdjacentHTML|document\.write|eval\(|new Function/);
    assert.doesNotMatch(code, /\bconsole\./);
    assert.doesNotMatch(code, /method\s*:|XMLHttpRequest|sendBeacon/);
    assert.doesNotMatch(code, /v1\/traces|resourceSpans/); // it has no way to build a trace
});
