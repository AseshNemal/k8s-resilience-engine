// Logic tests for autoconnect.js: when the page renews, what it does after failures, restarts and
// revocation, and that it never touches a credential. Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./autoconnect.js", import.meta.url), "utf8");
const window = {}; // no document: the script exposes its logic and stops before touching the DOM
vm.runInNewContext(source, { window, Set, Math, Date, JSON, Object, Promise, String, Boolean, Error, RegExp });
const A = window.__resilienceAuto;

// Objects created inside the vm sandbox have a different Object.prototype, which strict deepEqual
// rejects even when the contents are identical. Compare plain copies instead.
const plain = (value) => JSON.parse(JSON.stringify(value));

const CTX = "kind-resilience-research";
const TOKEN = "eyJ-TOKEN-THE-PAGE-MUST-NEVER-SEE";

// A fake helper and backend, and a controllable clock.
function world({ statuses, helper }) {
    const calls = [];
    const clock = { ms: 1_000_000 };
    const changes = [];
    let statusIndex = 0;
    const env = {
        platform: "darwin",
        now: () => clock.ms,
        getStatus: async () => {
            const s = statuses[Math.min(statusIndex, statuses.length - 1)];
            statusIndex += 1;
            return typeof s === "function" ? s() : s;
        },
        exec: async (file, args) => {
            calls.push({ file, args });
            const reply = typeof helper === "function" ? helper(args, calls.length) : helper;
            if (reply instanceof Error) throw reply;
            if (reply && reply.reject) { const e = new Error("exit 1"); e.stdout = reply.reject; throw e; }
            return { stdout: reply };
        },
        onChange: (s) => changes.push(s),
    };
    return { env, calls, clock, changes, controller: A.createController(env) };
}

const managed = (state, extra = {}) => ({ connected: true, managed: true, context: CTX, state, expiresAt: "2026-10-19T20:00:00Z", ...extra });
const ok = JSON.stringify({ ok: true, command: "renew", state: "connected", context: CTX });
const fail = (state) => JSON.stringify({ ok: false, command: "renew", state, message: "helper wording" });

// ------------------------------------------------------------------ when it renews

test("a credential that is not due is left alone and the helper is never started", async () => {
    const w = world({ statuses: [managed("ok")], helper: ok });
    const s = await w.controller.tick();
    assert.equal(s.phase, "ready");
    assert.equal(w.calls.length, 0);
});

test("a credential that is due is renewed once, with the context named by the backend", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: ok });
    const s = await w.controller.tick();
    assert.equal(s.phase, "ready");
    assert.deepEqual(plain(w.calls), [{ file: "resilience-helper", args: ["renew", CTX] }]);
});

test("an expired credential is renewed the same way: this is the recovery after the app was closed", async () => {
    const w = world({ statuses: [managed("expired", { expired: true })], helper: ok });
    await w.controller.tick();
    assert.deepEqual(plain(w.calls[0].args), ["renew", CTX]);
});

test("reopening the app later: a brand new controller with no memory renews immediately", async () => {
    // Docker Desktop restart, extension update or a long gap: the page starts from nothing.
    for (let i = 0; i < 3; i++) {
        const w = world({ statuses: [managed("expired", { expired: true })], helper: ok });
        const s = await w.controller.tick();
        assert.equal(s.phase, "ready");
        assert.equal(w.calls.length, 1);
    }
});

test("never connected, or connected by hand, means the page never starts the helper by itself", async () => {
    const none = world({ statuses: [{ connected: false }], helper: ok });
    assert.equal((await none.controller.tick()).phase, "needs-setup");
    const manual = world({ statuses: [{ connected: true, managed: false, state: "expired", expired: true }], helper: ok });
    assert.equal((await manual.controller.tick()).phase, "manual");
    assert.equal(none.calls.length + manual.calls.length, 0, "setup needs the user's approval; a manual credential is never auto-renewed");
});

test("an unreachable backend is reported and retried, and the helper is not started", async () => {
    const w = world({ statuses: [null], helper: ok });
    const s = await w.controller.tick();
    assert.equal(s.phase, "retrying");
    assert.equal(s.state, "backend-unavailable");
    assert.equal(w.calls.length, 0);
});

// ------------------------------------------------------------------ failures

test("a cluster that is still starting is retried with growing delays, never in a tight loop", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: fail("cluster-unreachable") });
    await w.controller.tick();
    assert.equal(w.calls.length, 1);
    await w.controller.tick(); // immediately again: still inside the 30 s back-off
    assert.equal(w.calls.length, 1, "must wait for the back-off");

    w.clock.ms += 31_000;
    await w.controller.tick();
    assert.equal(w.calls.length, 2);
    w.clock.ms += 31_000; // second delay is 60 s, so 31 s is too soon
    await w.controller.tick();
    assert.equal(w.calls.length, 2);
    w.clock.ms += 30_000;
    await w.controller.tick();
    assert.equal(w.calls.length, 3);
});

test("the back-off is capped at five minutes", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: fail("backend-unavailable") });
    for (let i = 0; i < 8; i++) { await w.controller.tick(); w.clock.ms += 301_000; }
    assert.equal(w.calls.length, 8, "after 5 minutes every check is allowed to try again");
    assert.deepEqual(plain(A.BACKOFF_MS.slice(-1)), [300000]);
});

test("success resets the back-off", async () => {
    let n = 0;
    const w = world({ statuses: [managed("renew-soon")], helper: () => (++n === 1 ? fail("cluster-unreachable") : ok) });
    await w.controller.tick();
    w.clock.ms += 31_000;
    assert.equal((await w.controller.tick()).phase, "ready");
});

test("removed, changed or over-granted access stops the loop until the person acts", async () => {
    for (const state of ["authorization-revoked", "drifted", "over-granted", "rbac-denied", "not-authorized"]) {
        const w = world({ statuses: [managed("renew-soon")], helper: fail(state) });
        const first = await w.controller.tick();
        assert.equal(first.phase, "attention");
        assert.equal(first.state, state);
        w.clock.ms += 3_600_000;
        await w.controller.tick();
        await w.controller.tick();
        assert.equal(w.calls.length, 1, `${state}: must not keep calling the helper once a person has to act`);
    }
});

test("revoked access is never silently recreated: the page only ever calls renew, never authorize", async () => {
    const w = world({ statuses: [managed("expired", { expired: true })], helper: fail("authorization-revoked") });
    for (let i = 0; i < 5; i++) { await w.controller.tick(); w.clock.ms += 400_000; }
    assert.ok(w.calls.every((c) => c.args[0] === "renew"), JSON.stringify(w.calls));
});

test("the block clears when the user acts, or when the backend now holds a different credential", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: fail("drifted") });
    await w.controller.tick();
    assert.equal(w.calls.length, 1);
    await w.controller.retryNow(); // the person pressed Try again
    assert.equal(w.calls.length, 2);

    const other = world({
        statuses: [managed("renew-soon"), managed("renew-soon", { context: "kind-other" })],
        helper: fail("drifted"),
    });
    await other.controller.tick();
    await other.controller.tick();
    assert.equal(other.calls.length, 2, "a different cluster is not blocked by the first one's problem");
});

test("a helper that cannot be started, or prints nonsense, is reported without crashing", async () => {
    for (const helper of [new Error("spawn ENOENT"), "not json at all", "", "[]", '{"ok":true}', "42"]) {
        const w = world({ statuses: [managed("renew-soon")], helper });
        const s = await w.controller.tick();
        assert.equal(s.state, "helper-unavailable", String(helper));
        assert.equal(s.phase, "retrying");
    }
});

test("a non-zero exit that still carried JSON is read, so a refusal is not mistaken for a crash", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: { reject: fail("authorization-revoked") } });
    assert.equal((await w.controller.tick()).state, "authorization-revoked");
});

test("two checks at once start the helper only once", async () => {
    let release;
    const gate = new Promise((r) => { release = r; });
    const w = world({ statuses: [managed("renew-soon")], helper: ok });
    const slow = w.env.exec;
    w.env.exec = async (...a) => { await gate; return slow(...a); };
    const c = A.createController(w.env);
    const first = c.tick();
    await Promise.resolve();
    const second = c.tick();
    release();
    await Promise.all([first, second]);
    assert.equal(w.calls.length, 1);
});

// ------------------------------------------------------------------ the user's own actions

test("setup passes the chosen context as one argument and reports a decline without treating it as an error", async () => {
    const w = world({ statuses: [{ connected: false }], helper: JSON.stringify({ ok: false, command: "authorize", state: "declined" }) });
    const r = await w.controller.setup(CTX);
    assert.equal(r.state, "declined");
    assert.deepEqual(plain(w.calls[0]), { file: "resilience-helper", args: ["authorize", CTX] });
    assert.notEqual(w.controller.snapshot().phase, "attention", "declining changes nothing, so the page is left as it was");
});

test("a hostile context name is passed as a single argument, never split or joined into a command", async () => {
    const hostile = `kind-x"; touch /tmp/pwned; "`;
    const w = world({ statuses: [{ connected: false }], helper: fail("unsupported-context") });
    await w.controller.setup(hostile);
    assert.deepEqual(plain(w.calls[0].args), ["authorize", hostile]);
    assert.equal(w.calls[0].args.length, 2);
});

test("a successful setup moves to ready", async () => {
    const w = world({ statuses: [{ connected: false }], helper: JSON.stringify({ ok: true, command: "authorize", state: "connected", context: CTX }) });
    await w.controller.setup(CTX);
    assert.equal(w.controller.snapshot().phase, "ready");
});

test("discover and remove call the helper with fixed arguments", async () => {
    const w = world({ statuses: [{ connected: false }], helper: JSON.stringify({ ok: true, command: "discover", state: "connected", contexts: [] }) });
    await w.controller.discover();
    await w.controller.remove(CTX);
    assert.deepEqual(plain(w.calls.map((c) => c.args)), [["discover"], ["revoke", CTX]]);
});

// ------------------------------------------------------------------ platform handling

test("automatic setup is offered only where it was verified, and says so honestly elsewhere", () => {
    assert.equal(A.platformSupport("darwin").automatic, true);
    for (const [platform, name] of [["win32", "Windows"], ["linux", "Linux"], ["", "this operating system"]]) {
        const s = A.platformSupport(platform);
        assert.equal(s.automatic, false);
        assert.match(s.note, new RegExp(`has not been verified on ${name}`));
        assert.match(s.note, /manual connection/);
    }
    assert.deepEqual(plain(A.VERIFIED_PLATFORMS), ["darwin"]);
});

test("the platform comes from Docker Desktop, with the browser as a fallback", () => {
    assert.equal(A.platformOf({ host: { platform: "win32" } }, { platform: "MacIntel" }), "win32");
    assert.equal(A.platformOf({}, { platform: "MacIntel" }), "darwin");
    assert.equal(A.platformOf(undefined, { platform: "Win32" }), "win32");
    assert.equal(A.platformOf(undefined, { platform: "Linux x86_64" }), "linux");
    assert.equal(A.platformOf(undefined, undefined), "");
});

test("the helper's file name follows the operating system", () => {
    assert.equal(A.helperFile("darwin"), "resilience-helper");
    assert.equal(A.helperFile("linux"), "resilience-helper");
    assert.equal(A.helperFile("win32"), "resilience-helper.exe");
});

// ------------------------------------------------------------------ credentials never reach the page

test("the page shows only its own fixed messages, never the helper's text", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: JSON.stringify({ ok: false, state: "cluster-unreachable", message: `leaked ${TOKEN}` }) });
    const s = await w.controller.tick();
    assert.ok(!JSON.stringify(s).includes(TOKEN) && !JSON.stringify(w.changes).includes(TOKEN));
    assert.equal(s.message, A.messageFor("cluster-unreachable"));
});

test("every state has a fixed, human message, and an unknown state falls back to a generic one", () => {
    for (const state of ["declined", "timed-out", "approval-unavailable", "cluster-unreachable", "rbac-denied", "over-granted", "drifted", "authorization-revoked", "helper-unavailable"]) {
        assert.ok(A.messageFor(state).length > 20, state);
    }
    assert.equal(A.messageFor("some-new-state"), A.messageFor("error"));
    assert.equal(A.messageFor(`<img src=x onerror=alert(1)>`), A.messageFor("error"), "unknown text is never shown");
});

test("the controller exposes no way to read a credential", () => {
    const c = A.createController({ platform: "darwin", now: () => 0, getStatus: async () => null, exec: async () => ({ stdout: "" }) });
    assert.deepEqual(plain(Object.keys(c).sort()), ["discover", "remove", "retryNow", "setup", "snapshot", "tick"]);
    assert.ok(!JSON.stringify(c.snapshot()).toLowerCase().includes("token"));
});
