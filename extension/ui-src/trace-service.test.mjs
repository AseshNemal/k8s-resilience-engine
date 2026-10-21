// Logic tests for trace-service.js: what it asks the host helper, that it can only ask for the three fixed
// commands, and that it shows its own wording, never helper or Docker text. Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./trace-service.js", import.meta.url), "utf8");
const window = {}; // no document: the script exposes its logic and stops before touching the DOM
vm.runInNewContext(source, { window, Set, Object, Promise, Boolean, Error, JSON, String });
const T = window.__resilienceTrace;
const plain = (value) => JSON.parse(JSON.stringify(value));

const reply = (state, ok = true, extra = {}) => JSON.stringify({ ok, command: "x", state, message: "HELPER WORDING DOCKER SAID BOOM", ...extra });

function service({ exec, platform = "darwin", sleep } = {}) {
    const calls = [];
    const env = { platform, sleep, exec: exec && (async (file, args) => { calls.push({ file, args }); return exec(file, args, calls.length); }) };
    return { calls, svc: T.createTraceService(env) };
}

test("without the host helper nothing is available and every call answers helper-unavailable", async () => {
    const { svc } = service({});
    assert.equal(svc.available, false);
    for (const call of [svc.status, svc.start, svc.stop]) {
        const r = await call();
        assert.equal(r.ok, false);
        assert.equal(r.state, "helper-unavailable");
    }
});

test("each call runs the helper with exactly one fixed command and no other argument", async () => {
    const { svc, calls } = service({ exec: () => ({ stdout: reply("trace-running") }) });
    await svc.status();
    await svc.start();
    await svc.stop();
    assert.deepEqual(plain(calls), [
        { file: "resilience-helper", args: ["trace-status"] },
        { file: "resilience-helper", args: ["trace-start"] },
        { file: "resilience-helper", args: ["trace-stop"] },
    ]);
});

test("the API takes no arguments, so a page cannot ask the helper for anything else", async () => {
    const { svc, calls } = service({ exec: () => ({ stdout: reply("trace-running") }) });
    await svc.start("--image", "evil", "; rm -rf /");
    assert.deepEqual(plain(calls), [{ file: "resilience-helper", args: ["trace-start"] }]);
});

test("Windows runs the .exe", async () => {
    const { svc, calls } = service({ platform: "win32", exec: () => ({ stdout: reply("trace-running") }) });
    await svc.start();
    assert.equal(calls[0].file, "resilience-helper.exe");
});

test("only the state is taken from the helper; the wording shown is this file's own", async () => {
    const { svc } = service({ exec: () => ({ stdout: reply("trace-start-failed", false) }) });
    const r = await svc.start();
    assert.equal(r.ok, false);
    assert.equal(r.state, "trace-start-failed");
    assert.equal(r.message, "Docker could not start the trace service.");
    assert.ok(!r.message.includes("BOOM"));
});

test("every state the helper can print has its own message", () => {
    for (const state of ["trace-running", "trace-running-external", "trace-stopped", "trace-not-created", "trace-not-ready",
        "trace-port-in-use", "trace-start-failed", "docker-unavailable"]) {
        assert.notEqual(T.messageFor(state), T.messageFor("not-a-state"), state);
    }
});

test("a state it does not know is shown as the helper being unavailable, never as the helper's own text", async () => {
    const { svc } = service({ exec: () => ({ stdout: reply("surprise-state") }) });
    const r = await svc.start();
    assert.equal(r.message, T.messageFor("helper-unavailable"));
    assert.ok(!r.message.includes("BOOM"));
});

test("the helper's output is read from a rejection too, because a non-zero exit can arrive that way", async () => {
    const { svc } = service({ exec: () => { const e = new Error("exit 1"); e.stdout = reply("trace-port-in-use", false); throw e; } });
    const r = await svc.start();
    assert.equal(r.state, "trace-port-in-use");
});

test("unreadable output becomes helper-unavailable", async () => {
    for (const out of ["", "not json", "{}", '{"ok":true}', null, undefined]) {
        const { svc } = service({ exec: () => ({ stdout: out }) });
        assert.equal((await svc.start()).state, "helper-unavailable", String(out));
    }
});

test("a helper that never answers is given up on, with a clear state", async () => {
    let wake;
    const sleep = (ms) => new Promise((resolve) => { wake = { ms, resolve }; });
    const { svc } = service({ exec: () => new Promise(() => {}), sleep });
    const pending = svc.start();
    await Promise.resolve();
    await Promise.resolve();
    assert.equal(wake.ms, T.WAIT_MS["trace-start"], "start is allowed long enough to pull the image");
    wake.resolve();
    const r = await pending;
    assert.equal(r.state, "timed-out");
    assert.equal(r.ok, false);
});

test("a second call while one is running shares its answer instead of starting another helper", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const { svc, calls } = service({ exec: async () => { await gate; return { stdout: reply("trace-running") }; } });
    const first = svc.start();
    const second = svc.start();
    const third = svc.stop();
    release();
    const results = await Promise.all([first, second, third]);
    assert.equal(calls.length, 1);
    assert.deepEqual(results.map((r) => r.state), ["trace-running", "trace-running", "trace-running"]);
});

test("after a call finishes the next one runs the helper again", async () => {
    const { svc, calls } = service({ exec: () => ({ stdout: reply("trace-running") }) });
    await svc.status();
    await svc.status();
    assert.equal(calls.length, 2);
});

test("a helper that throws synchronously is reported, not raised", async () => {
    const env = { platform: "darwin", exec: () => { throw new Error("no such file"); } };
    const r = await T.createTraceService(env).start();
    assert.equal(r.state, "helper-unavailable");
});

test("the platform comes from Docker Desktop, with the browser's own hint as a fallback", () => {
    assert.equal(T.platformOf({ host: { platform: "win32" } }, {}), "win32");
    assert.equal(T.platformOf({}, { platform: "MacIntel" }), "darwin");
    assert.equal(T.platformOf({}, { platform: "Linux x86_64" }), "linux");
    assert.equal(T.platformOf(null, {}), "");
});
