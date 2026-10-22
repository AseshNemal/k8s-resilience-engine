// Logic tests for bookinfo-tracing.js: which buttons make sense, that the page reduces helper output to known fields,
// the order of the flows, and that "verified" is only ever said for traces Jaeger really holds.
// Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./bookinfo-tracing.js", import.meta.url), "utf8");
const window = {}; // no document: the script exposes its logic and stops before touching the DOM
const context = { window, Set, Object, Promise, Boolean, Error, JSON, String, Number, Date, Math, Array, Infinity, setTimeout, Number };
vm.runInNewContext(source, context);
const B = window.__resilienceBookinfo;
const plain = (value) => JSON.parse(JSON.stringify(value));

const readyPlan = { ready: true, blockers: [], warnings: [], preview: [{ step: 1, action: "install", kind: "Istio 1.31.2", name: "istiod", detail: "32 objects" }],
    disruptions: [{ workload: "ratings", pod: "ratings-v1-x", effect: "the reviews show \"ratings unavailable\"" }], jaeger: { ipv4: "192.168.65.254", port: "4317", nodes: ["a"] }, gate0: { proceed: true } };
const setup = (phase, extra = {}) => ({ phase, steps: [], created: 0, deleted: 0, kept: 0, foreign: 0, remaining: [], ...extra });

// ---- the buttons ----

const view = (over) => ({ busy: "", helperAvailable: true, context: "docker-desktop", plan: null, setup: null, ...over });

test("Plan is the only action before anything is set up, and Enable needs a ready plan", () => {
    assert.deepEqual(plain(B.availability(view())), { plan: true, enable: false, verify: false, traffic: false, undo: false });
    assert.equal(B.availability(view({ plan: B.cleanPlan(readyPlan) })).enable, true);
    assert.equal(B.availability(view({ plan: B.cleanPlan({ ...readyPlan, ready: false, blockers: [{ code: "X", message: "m" }] }) })).enable, false);
});

test("Verify needs an applied setup; Undo is offered for anything that is not removed", () => {
    for (const [phase, want] of [["applied", { plan: false, enable: false, verify: true, traffic: true, undo: true }],
        ["applying", { plan: false, enable: false, verify: false, traffic: false, undo: true }], ["rolling-back", { plan: false, enable: false, verify: false, traffic: false, undo: true }],
        ["rollback-failed", { plan: false, enable: false, verify: false, traffic: false, undo: true }], ["rolled-back", { plan: true, enable: false, verify: false, traffic: false, undo: false }]]) {
        assert.deepEqual(plain(B.availability(view({ setup: setup(phase) }))), want, phase);
    }
});

test("nothing is available while something runs, without a helper, or without a cluster", () => {
    const none = { plan: false, enable: false, verify: false, traffic: false, undo: false };
    assert.deepEqual(plain(B.availability(view({ busy: "enable", setup: setup("applied") }))), none);
    assert.deepEqual(plain(B.availability(view({ helperAvailable: false }))), none);
    assert.deepEqual(plain(B.availability(view({ context: "" }))), none);
});

// ---- reducing helper output ----

test("only known fields survive, and an unknown phase is not a setup", () => {
    const cleaned = B.cleanResult({ ok: true, state: "bookinfo-applied", message: "m".repeat(5000), token: "SECRET", plan: { ...readyPlan, token: "SECRET" },
        setup: { ...setup("applied"), kubeconfig: "SECRET" } });
    assert.ok(!JSON.stringify(cleaned).includes("SECRET"));
    assert.equal(cleaned.message.length, 900);
    assert.equal(B.cleanSetup({ phase: "hacked" }), null);
    assert.equal(B.cleanSetup(null), null);
    assert.equal(B.cleanPlan("x"), null);
});

test("the helper's last line is the result, and anything else is nothing", () => {
    assert.equal(B.parseHelperOutput('noise\n{"ok":true,"state":"s"}\n').state, "s");
    assert.equal(B.parseHelperOutput("not json"), null);
    assert.equal(B.parseHelperOutput('{"ok":true}'), null);
    assert.equal(B.parseHelperOutput(5), null);
});

test("a context name is only refused if it cannot be a name", () => {
    assert.equal(B.validContext("docker-desktop"), true);
    assert.equal(B.validContext("kind-resilience research"), true);
    for (const bad of ["", "a\nb", "a\u0000b", "x".repeat(254), null, 5]) assert.equal(B.validContext(bad), false);
});

// ---- the helper bridge ----

test("the bridge passes only the command and the context as arguments, and only known commands", async () => {
    const calls = [];
    const helper = B.createHelper({ platform: "darwin", exec: (file, args) => { calls.push([file, args]); return Promise.resolve({ stdout: '{"ok":true,"state":"bookinfo-status"}' }); } });
    const r = await helper.run("trace-bookinfo-status", "docker-desktop");
    assert.equal(r.ok, true);
    assert.deepEqual(plain(calls), [["resilience-helper", ["trace-bookinfo-status", "docker-desktop"]]]);
    assert.equal((await helper.run("rm -rf /", "docker-desktop")).state, "helper-unavailable");
    assert.equal((await helper.run("trace-bookinfo-apply", "a\nb")).state, "helper-unavailable");
    assert.equal(calls.length, 1, "nothing else reached the helper");
    const windows = B.createHelper({ platform: "win32", exec: (file) => { calls.push([file]); return Promise.resolve({ stdout: "" }); } });
    await windows.run("trace-bookinfo-plan", "docker-desktop");
    assert.equal(calls.at(-1)[0], "resilience-helper.exe");
});

test("without Docker Desktop's exec nothing is available and nothing is attempted", async () => {
    const helper = B.createHelper({ platform: "linux", exec: null });
    assert.equal(helper.available, false);
    assert.equal((await helper.run("trace-bookinfo-plan", "docker-desktop")).state, "helper-unavailable");
});

test("a helper that does not answer in time is reported with the way out", async () => {
    const helper = B.createHelper({ platform: "darwin", exec: () => new Promise(() => {}), sleep: () => Promise.resolve() });
    const r = await helper.run("trace-bookinfo-verify", "docker-desktop");
    assert.equal(r.state, "timed-out");
    assert.match(r.message, /use Undo if it did not finish/);
});

test("a non-zero exit that still carries the helper's output is read", async () => {
    const helper = B.createHelper({ platform: "darwin", exec: () => Promise.reject({ stdout: '{"ok":false,"state":"declined","message":"you declined"}' }) });
    const r = await helper.run("trace-bookinfo-apply", "docker-desktop");
    assert.equal(r.state, "declined");
});

// ---- the flows ----

function fakeEnv(script) {
    const log = [];
    const results = { ...script.results };
    let t = 0;
    const env = {
        helper: { available: true, run: async (command, ctx) => { log.push(`${command} ${ctx}`); const r = results[command]; return typeof r === "function" ? r() : r; } },
        context: async () => script.context ?? "docker-desktop",
        fetchJson: async (url) => { log.push(`GET ${url}`); const next = script.traces.length > 1 ? script.traces.shift() : script.traces[0]; return { status: 200, body: next }; },
        sleep: async (ms) => { t += ms; },
        now: () => t,
        every: (fn, ms) => { log.push(`poll ${ms}`); return () => log.push("poll stopped"); },
        onUpdate: (v) => log.push(`busy=${v.busy}`),
    };
    return { env, log };
}

const ok = (state, extra = {}) => ({ ok: true, state, message: "", plan: null, setup: null, verify: null, ...extra });

test("Enable tracing needs a ready plan first, asks the helper once, and polls its record while it works", async () => {
    const { env, log } = fakeEnv({ results: {
        "trace-bookinfo-status": ok("bookinfo-status"), "trace-bookinfo-plan": ok("bookinfo-plan-ready", { plan: B.cleanPlan(readyPlan) }),
        "trace-bookinfo-apply": ok("bookinfo-applied", { setup: B.cleanSetup(setup("applied")) }) }, traces: [] });
    const c = B.createController(env);
    await c.refresh();
    await c.enable();
    assert.ok(!log.some((l) => l.startsWith("trace-bookinfo-apply")), "Enable must do nothing before a plan is ready");
    await c.plan();
    await c.enable();
    assert.equal(log.filter((l) => l.startsWith("trace-bookinfo-apply")).length, 1);
    assert.ok(log.indexOf("poll 3000") < log.findIndex((l) => l.startsWith("trace-bookinfo-apply")) + 1 && log.includes("poll stopped"));
    assert.equal(c.view().setup.phase, "applied");
    assert.equal(c.view().plan, null, "a plan describes the cluster before the change");
    assert.equal(c.view().busy, "");
});

test("a blocked plan never enables the setup", async () => {
    const blocked = B.cleanPlan({ ...readyPlan, ready: false, blockers: [{ code: "ISTIO_ALREADY_PRESENT", message: "Istio is already installed" }] });
    const { env, log } = fakeEnv({ results: { "trace-bookinfo-status": ok("bookinfo-status"), "trace-bookinfo-plan": { ok: false, state: "bookinfo-plan-blocked", message: "", plan: blocked } }, traces: [] });
    const c = B.createController(env);
    await c.refresh();
    await c.plan();
    await c.enable();
    assert.equal(c.view().plan.blockers[0].code, "ISTIO_ALREADY_PRESENT");
    assert.ok(!log.some((l) => l.startsWith("trace-bookinfo-apply")));
});

const partialTrace = { verdict: "PARTIAL", edges: [{ from: "productpage", to: "details", traces: 3 }], missingEdges: ["reviews->ratings"], servicesReporting: ["productpage", "details"], missingServices: ["reviews", "ratings"], tracesExamined: 3, reviewsVersion: { v2: 0, v1: 0, unknown: 3 } };
const verifiedTrace = { verdict: "VERIFIED", edges: [{ from: "productpage", to: "details", traces: 12 }], missingEdges: [], servicesReporting: ["productpage", "details", "reviews", "ratings"], missingServices: [], tracesExamined: 12, reviewsVersion: { v2: 12, v1: 0, unknown: 0 } };
const appliedStatus = () => ok("bookinfo-status", { setup: B.cleanSetup(setup("applied")) });

test("Verify only reads: it never runs the traffic command and reads Jaeger once, without waiting", async () => {
    const { env, log } = fakeEnv({ results: { "trace-bookinfo-status": appliedStatus(),
        "trace-bookinfo-verify": ok("bookinfo-checks-passed", { verify: B.cleanVerify({ requestsSent: false, instrumented: [{ workload: "ratings-v1", pod: "ratings-v1-x", sidecar: true, ready: true }] }) }) }, traces: [partialTrace] });
    const c = B.createController(env);
    await c.refresh();
    await c.verify();
    assert.ok(!log.some((l) => l.startsWith("trace-bookinfo-traffic")), "Verify must never create the client Pod");
    assert.ok(!log.some((l) => l.startsWith("trace-bookinfo-apply") || l.startsWith("trace-bookinfo-undo")));
    assert.equal(log.filter((l) => l.startsWith("GET")).length, 1, "Verify reads Jaeger once and does not wait for traffic");
    assert.equal(c.view().verify.instrumented.length, 1);
    assert.equal(c.view().trace.verdict, "PARTIAL");
});

test("Run test traffic is its own operation, then waits for the traces to be linked", async () => {
    const { env, log } = fakeEnv({ results: { "trace-bookinfo-status": appliedStatus(),
        "trace-bookinfo-traffic": ok("bookinfo-traffic-sent", { verify: B.cleanVerify({ requestsSent: true, instrumented: [] }) }) }, traces: [partialTrace, partialTrace, verifiedTrace] });
    const c = B.createController(env);
    await c.refresh();
    await c.traffic();
    assert.equal(log.filter((l) => l.startsWith("trace-bookinfo-traffic")).length, 1);
    assert.ok(!log.some((l) => l.startsWith("trace-bookinfo-verify")));
    assert.equal(log.filter((l) => l.startsWith("GET")).length, 3);
    assert.equal(c.view().trace.verdict, "VERIFIED");
    assert.match(B.describeTrace(c.view().trace).headline, /^Verified:/);
    assert.ok(B.describeChecks(c.view().verify).some((l) => /temporary Pod .* removed/.test(l.text)));
});

test("traces that never become linked are reported as partial, never as verified", async () => {
    const { env } = fakeEnv({ results: { "trace-bookinfo-status": appliedStatus(), "trace-bookinfo-traffic": ok("bookinfo-traffic-sent") }, traces: [partialTrace] });
    const c = B.createController(env);
    await c.refresh();
    await c.traffic();
    const shown = B.describeTrace(c.view().trace);
    assert.equal(shown.kind, "warn");
    assert.doesNotMatch(shown.headline, /Verified/);
    assert.ok(shown.lines.some((l) => /No span from: reviews, ratings/.test(l)));
    assert.ok(shown.lines.some((l) => /Calls not seen: reviews->ratings/.test(l)));
});

test("a declined or failed traffic run does not read Jaeger or claim anything", async () => {
    for (const result of [{ ok: false, state: "declined", message: "you declined, so nothing was changed", plan: null, setup: null, verify: null },
        { ok: false, state: "bookinfo-verify-failed", message: "The Jaeger address changed", plan: null, setup: null, verify: null }]) {
        const { env, log } = fakeEnv({ results: { "trace-bookinfo-status": appliedStatus(), "trace-bookinfo-traffic": result }, traces: [] });
        const c = B.createController(env);
        await c.refresh();
        await c.traffic();
        assert.ok(!log.some((l) => l.startsWith("GET")));
        assert.equal(c.view().trace, null);
        assert.equal(c.view().outcome, "fail");
        assert.equal(c.view().message, result.message);
    }
});

test("a failed Verify shows its reason and claims nothing", async () => {
    const { env } = fakeEnv({ results: { "trace-bookinfo-status": appliedStatus(),
        "trace-bookinfo-verify": { ok: false, state: "bookinfo-verify-failed", message: "productpage-x has no working sidecar", plan: null, setup: null, verify: B.cleanVerify({ instrumented: [{ workload: "productpage-v1", pod: "productpage-x", sidecar: false, ready: true }] }) } }, traces: [partialTrace] });
    const c = B.createController(env);
    await c.refresh();
    await c.verify();
    assert.equal(c.view().outcome, "fail");
    assert.equal(c.view().trace, null);
    assert.equal(B.describeChecks(c.view().verify)[0].ok, false);
});

test("the checks name every Pod and the Jaeger address, and report drift", () => {
    const lines = B.describeChecks(B.cleanVerify({ appliedAddress: "192.168.65.254", currentAddress: "192.168.65.1", addressDrift: true,
        instrumented: [{ workload: "ratings-v1", pod: "ratings-v1-x", sidecar: true, ready: true }] }));
    assert.equal(lines[0].ok, true);
    assert.equal(lines[1].ok, false);
    assert.match(lines[1].text, /192\.168\.65\.254.*192\.168\.65\.1/);
    assert.deepEqual(plain(B.describeChecks(null)), []);
});

test("the traffic command is known to the bridge and passed only the context", async () => {
    const calls = [];
    const helper = B.createHelper({ platform: "darwin", exec: (file, args) => { calls.push(args); return Promise.resolve({ stdout: '{"ok":true,"state":"bookinfo-traffic-sent"}' }); } });
    assert.equal((await helper.run("trace-bookinfo-traffic", "docker-desktop")).state, "bookinfo-traffic-sent");
    assert.deepEqual(plain(calls), [["trace-bookinfo-traffic", "docker-desktop"]]);
    assert.ok("trace-bookinfo-traffic" in B.WAIT_MS && B.WAIT_MS["trace-bookinfo-verify"] < B.WAIT_MS["trace-bookinfo-traffic"], "Verify is quick; the traffic run waits for a dialog and a Pod");
});

test("Undo clears what was shown and reads the record again", async () => {
    let phase = "applied";
    const { env } = fakeEnv({ results: { "trace-bookinfo-status": () => ok("bookinfo-status", { setup: B.cleanSetup(setup(phase)) }),
        "trace-bookinfo-undo": () => { phase = "rolled-back"; return ok("bookinfo-undone"); } }, traces: [] });
    const c = B.createController(env);
    await c.refresh();
    assert.equal(c.view().setup.phase, "applied");
    await c.undo();
    assert.equal(c.view().setup.phase, "rolled-back");
    assert.equal(c.view().verify, null);
});

test("a second click while a command runs is ignored", async () => {
    let release;
    const { env, log } = fakeEnv({ results: { "trace-bookinfo-status": ok("bookinfo-status"), "trace-bookinfo-plan": ok("bookinfo-plan-ready", { plan: B.cleanPlan(readyPlan) }),
        "trace-bookinfo-apply": () => new Promise((resolve) => { release = () => resolve(ok("bookinfo-applied", { setup: B.cleanSetup(setup("applied")) })); }) }, traces: [] });
    const c = B.createController(env);
    await c.refresh();
    await c.plan();
    const first = c.enable();
    await new Promise((r) => setTimeout(r, 0));
    await c.enable();
    release();
    await first;
    assert.equal(log.filter((l) => l.startsWith("trace-bookinfo-apply")).length, 1);
});

test("the headline always says what is true", () => {
    assert.match(B.headline(view({ helperAvailable: false })), /helper is not available/);
    assert.match(B.headline(view({ context: "" })), /Connect a cluster first/);
    assert.match(B.headline(view({ busy: "enable" })), /Waiting for your approval/);
    assert.match(B.headline(view({ busy: "verify" })), /nothing is changed/);
    assert.match(B.headline(view({ busy: "traffic" })), /Waiting for your approval in the system dialog, then sending real requests/);
    assert.match(B.headline(view({ setup: setup("applying") })), /did not finish/);
    assert.match(B.headline(view({ setup: setup("rollback-failed") })), /could not be removed/);
});

test("nothing is written as HTML and nothing is evaluated", () => {
    assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function/.test(source));
    assert.ok(!/localStorage|sessionStorage/.test(source), "no state is kept in the page");
});
