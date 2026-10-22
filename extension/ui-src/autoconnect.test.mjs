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
function world({ statuses, helper, sleep }) {
    const renewed = [];
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
            const reply = await (typeof helper === "function" ? helper(args, calls.length) : helper);
            if (reply instanceof Error) throw reply;
            if (reply && reply.reject) { const e = new Error("exit 1"); e.stdout = reply.reject; throw e; }
            return { stdout: reply };
        },
        onChange: (s) => changes.push(s),
    };
    if (sleep) env.sleep = sleep;
    env.onRenewed = () => renewed.push(1);
    return { renewed, env, calls, clock, changes, controller: A.createController(env) };
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

// ------------------------------------------------------------------ one-click setup from the banner

const listing = (contexts) => JSON.stringify({ ok: true, command: "discover", state: "connected", contexts });
const kind = (name, port, extra = {}) => ({ name, server: `https://127.0.0.1:${port}`, supported: true, reachable: true, ...extra });
const authorized = JSON.stringify({ ok: true, command: "authorize", state: "connected", context: CTX });

function quickWorld(contexts, authorizeReply = authorized) {
    return world({ statuses: [{ connected: true, managed: false }], helper: (args) => (args[0] === "discover" ? listing(contexts) : authorizeReply) });
}

test("quick setup finds the cluster the stored credential points at, by port, and authorizes only that one", async () => {
    const w = quickWorld([kind("other", 6443), kind(CTX, 58227)]);
    const r = await w.controller.quickSetup("host.docker.internal:58227");
    assert.equal(r.ok, true);
    assert.deepEqual(plain(w.calls.map((c) => c.args)), [["discover"], ["authorize", CTX]]);
    assert.equal(w.controller.snapshot().phase, "ready");
});

test("quick setup uses the only usable cluster when the stored server is not recognised", async () => {
    const w = quickWorld([kind(CTX, 58227), kind("down", 9999, { reachable: false }), kind("remote", 1, { supported: false })]);
    const r = await w.controller.quickSetup("host.docker.internal:1234");
    assert.equal(r.ok, true);
    assert.deepEqual(plain(w.calls[1].args), ["authorize", CTX]);
});

test("quick setup never guesses between several clusters: it asks the person to choose", async () => {
    const w = quickWorld([kind("a", 1111), kind("b", 2222)]);
    const r = await w.controller.quickSetup("host.docker.internal:58227");
    assert.equal(r.state, "choose");
    assert.deepEqual(plain(w.calls.map((c) => c.args)), [["discover"]], "nothing is authorized without a clear target");
});

test("quick setup with no usable cluster says the cluster is unreachable and authorizes nothing", async () => {
    const w = quickWorld([kind(CTX, 58227, { reachable: false })]);
    const r = await w.controller.quickSetup("host.docker.internal:58227");
    assert.equal(r.state, "cluster-unreachable");
    assert.equal(w.calls.length, 1);
});

test("quick setup passes a failed discovery through and a decline is not an error", async () => {
    const down = world({ statuses: [{ connected: true }], helper: JSON.stringify({ ok: false, command: "discover", state: "helper-unavailable" }) });
    assert.equal((await down.controller.quickSetup("h:1")).state, "helper-unavailable");
    const declined = quickWorld([kind(CTX, 58227)], JSON.stringify({ ok: false, command: "authorize", state: "declined" }));
    assert.equal((await declined.controller.quickSetup("host.docker.internal:58227")).state, "declined");
    assert.notEqual(declined.controller.snapshot().phase, "attention");
});

// ------------------------------------------------------------------ platform handling

test("automatic setup is offered on macOS, Windows and Linux, and says so honestly elsewhere", () => {
    for (const platform of ["darwin", "win32", "linux"]) {
        const s = A.platformSupport(platform);
        assert.equal(s.automatic, true, platform);
        assert.equal(s.note, "");
    }
    for (const platform of ["", "freebsd"]) {
        const s = A.platformSupport(platform);
        assert.equal(s.automatic, false);
        assert.match(s.note, /not available on this operating system/);
        assert.match(s.note, /manual connection/);
    }
    assert.deepEqual(plain(A.ENABLED_PLATFORMS), ["darwin", "win32", "linux"]);
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
    for (const state of ["declined", "timed-out", "approval-unavailable", "cluster-unreachable", "rbac-denied", "over-granted", "drifted", "authorization-revoked", "helper-unavailable", "too-slow", "background-unsupported"]) {
        assert.ok(A.messageFor(state).length > 20, state);
    }
    assert.equal(A.messageFor("some-new-state"), A.messageFor("error"));
    assert.equal(A.messageFor(`<img src=x onerror=alert(1)>`), A.messageFor("error"), "unknown text is never shown");
});

test("the controller exposes no way to read a credential", () => {
    const c = A.createController({ platform: "darwin", now: () => 0, getStatus: async () => null, exec: async () => ({ stdout: "" }) });
    assert.deepEqual(plain(Object.keys(c).sort()), ["discover", "enableBackground", "quickSetup", "refreshBackground", "remove", "retryNow", "setup", "snapshot", "tick"]);
    assert.ok(!JSON.stringify(c.snapshot()).toLowerCase().includes("token"));
});


// ------------------------------------------------------------------ a slow Docker never wedges the loop

const never = () => new Promise(() => {}); // a call that never answers
const instantly = () => Promise.resolve(); // a watchdog timer that has already run out

test("a helper call that never answers is given up on, so the loop can try again instead of hanging forever", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: never, sleep: instantly });
    const s = await w.controller.tick(); // must resolve, not hang
    assert.equal(s.phase, "retrying");
    assert.equal(s.state, "too-slow");
    assert.match(s.message, /slow/i);
});

test("after a hung call the next check is allowed to run: the single-flight guard is released", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: never, sleep: instantly });
    await w.controller.tick();
    w.clock.ms += 31_000;
    await w.controller.tick();
    assert.equal(w.calls.length, 2, "a hung renewal must not block every later renewal");
});

test("a status request that never answers is reported as the backend not answering, and the check ends", async () => {
    const w = world({ statuses: [never], helper: ok, sleep: instantly });
    const s = await w.controller.tick();
    assert.equal(s.phase, "retrying");
    assert.equal(s.state, "backend-unavailable");
    assert.equal(w.calls.length, 0);
});

test("a slow answer is not mistaken for a hang: a call that finishes in time is used", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: ok, sleep: never }); // the watchdog never fires
    assert.equal((await w.controller.tick()).phase, "ready");
});

test("the helper saying it ran out of time is retried with back-off, never treated as something the person must fix", async () => {
    const w = world({ statuses: [managed("renew-soon")], helper: fail("too-slow") });
    const s = await w.controller.tick();
    assert.equal(s.phase, "retrying");
    assert.equal(s.state, "too-slow");
    await w.controller.tick();
    assert.equal(w.calls.length, 1, "still inside the back-off");
});

test("each helper command has its own wait, long enough for the helper's own deadline", () => {
    const wait = A.WAIT_MS;
    assert.ok(wait.renew >= 5 * 60 * 1000, "the helper itself allows 4 minutes for a renewal");
    assert.ok(wait.authorize > wait.renew || wait.authorize >= 5 * 60 * 1000, "setup includes a dialog the person must answer");
    assert.ok(wait.status >= 20 * 1000);
});

// ------------------------------------------------------------------ the background renewal job

const agentReply = (background, extra = {}) => JSON.stringify({ ok: true, command: "agent-status", state: "idle", background, ...extra });

test("the background job state is unknown until it is asked for, and asking is read-only", async () => {
    const w = world({ statuses: [managed("ok")], helper: agentReply("off") });
    assert.equal(w.controller.snapshot().background, "");
    await w.controller.refreshBackground();
    assert.equal(w.controller.snapshot().background, "off");
    assert.deepEqual(plain(w.calls), [{ file: "resilience-helper", args: ["agent-status"] }]);
});

test("a helper that cannot answer leaves the job state unknown rather than guessing", async () => {
    const w = world({ statuses: [managed("ok")], helper: new Error("no helper") });
    await w.controller.refreshBackground();
    assert.equal(w.controller.snapshot().background, "");
});

test("turning the job on asks the helper for exactly that cluster and records the answer", async () => {
    const w = world({ statuses: [managed("ok")], helper: JSON.stringify({ ok: true, command: "agent-enable", state: "connected", background: "on" }) });
    const r = await w.controller.enableBackground(CTX);
    assert.equal(r.ok, true);
    assert.deepEqual(plain(w.calls[0]), { file: "resilience-helper", args: ["agent-enable", CTX] });
    assert.equal(w.controller.snapshot().background, "on");
});

test("declining the job's dialog changes nothing and is not an error", async () => {
    const w = world({ statuses: [managed("ok")], helper: JSON.stringify({ ok: false, command: "agent-enable", state: "declined" }) });
    await w.controller.refreshBackground();
    const before = w.controller.snapshot();
    await w.controller.enableBackground(CTX);
    const after = w.controller.snapshot();
    assert.equal(after.background, before.background);
    assert.notEqual(after.phase, "attention");
});

test("a successful setup that also installed the job is remembered", async () => {
    const w = world({ statuses: [{ connected: false }], helper: JSON.stringify({ ok: true, command: "authorize", state: "connected", context: CTX, background: "on" }) });
    await w.controller.setup(CTX);
    assert.equal(w.controller.snapshot().background, "on");
});

test("the job is only ever turned on by an explicit call, never by a check", async () => {
    const w = world({ statuses: [managed("expired", { expired: true })], helper: ok });
    for (let i = 0; i < 3; i++) { await w.controller.tick(); w.clock.ms += 400_000; }
    assert.ok(w.calls.every((c) => c.args[0] === "renew"), "checks may only ever renew");
});


// ------------------------------------------------------------------ pages recover by themselves

test("a successful renewal tells the page, so a page stuck on an access error can load again without a click", async () => {
    const w = world({ statuses: [managed("expired", { expired: true })], helper: ok });
    await w.controller.tick();
    assert.equal(w.renewed.length, 1);
});

test("the page is told nothing when no renewal happened or it failed", async () => {
    const notDue = world({ statuses: [managed("ok")], helper: ok });
    await notDue.controller.tick();
    const failed = world({ statuses: [managed("renew-soon")], helper: fail("too-slow") });
    await failed.controller.tick();
    assert.equal(notDue.renewed.length + failed.renewed.length, 0);
});

// ------------------------------------------------------------------ reconnecting a cluster that was approved earlier

const APPROVED = { context: CTX, server: "127.0.0.1:58227", caSha256: "ab".repeat(32), disconnectedByUser: false };
const lost = (approved = APPROVED) => ({ connected: false, approved });
const reconnected = JSON.stringify({ ok: true, command: "reconnect", state: "connected", context: CTX, tokenRequest: true });
const idle = JSON.stringify({ ok: true, command: "reconnect", state: "idle", message: "nothing to reconnect" });

test("a connection lost for a cluster the person approved is restored by the helper, with no argument and no dialog", async () => {
    const w = world({ statuses: [lost()], helper: reconnected });
    const s = await w.controller.tick();
    assert.equal(s.phase, "ready");
    assert.deepEqual(plain(w.calls), [{ file: "resilience-helper", args: ["reconnect"] }]);
    assert.equal(w.renewed.length, 1, "pages that loaded without access retry themselves");
});

test("a cluster the person disconnected is never reconnected, on any reload", async () => {
    // Each iteration is a fresh page load or extension restart: a new controller with no memory.
    for (let i = 0; i < 4; i++) {
        const w = world({ statuses: [lost({ ...APPROVED, disconnectedByUser: true })], helper: reconnected });
        for (let j = 0; j < 3; j++) {
            const s = await w.controller.tick();
            assert.equal(s.phase, "needs-setup");
        }
        assert.equal(w.calls.length, 0, "the helper must not even be started");
    }
});

test("nothing approved means nothing is reconnected, and a first connection always waits for the person", async () => {
    const w = world({ statuses: [{ connected: false }], helper: reconnected });
    assert.equal((await w.controller.tick()).phase, "needs-setup");
    assert.equal(w.calls.length, 0);
});

test("a connected cluster is only renewed, never reconnected", async () => {
    const w = world({ statuses: [managed("ok", { approved: APPROVED })], helper: reconnected });
    await w.controller.tick();
    assert.equal(w.calls.length, 0);
});

for (const state of ["identity-changed", "authorization-revoked", "drifted", "over-granted", "rbac-denied", "not-authorized", "unsupported-context"]) {
    test(`a reconnect that stops with ${state} waits for the person instead of retrying`, async () => {
        const w = world({ statuses: [lost()], helper: fail(state) });
        const first = await w.controller.tick();
        assert.equal(first.phase, "attention");
        assert.equal(first.state, state);
        w.clock.ms += 3_600_000;
        await w.controller.tick();
        await w.controller.tick();
        assert.equal(w.calls.length, 1, "a problem only a person can fix must not be retried");
        assert.notEqual(A.messageFor(state), A.messageFor("error"), "and it must be explained, not shown as a generic error");
    });
}

for (const state of ["cluster-unreachable", "backend-unavailable", "too-slow", "busy"]) {
    test(`a reconnect that stops with ${state} is retried with growing delays`, async () => {
        const w = world({ statuses: [lost()], helper: fail(state) });
        const first = await w.controller.tick();
        assert.equal(first.phase, "retrying");
        await w.controller.tick();
        assert.equal(w.calls.length, 1, "inside the back-off");
        w.clock.ms += 31_000;
        await w.controller.tick();
        assert.equal(w.calls.length, 2);
        w.clock.ms += 31_000;
        await w.controller.tick();
        assert.equal(w.calls.length, 2, "the second delay is longer");
    });
}

test("when the helper says there is nothing to reconnect, the page waits quietly instead of asking every minute", async () => {
    const w = world({ statuses: [lost()], helper: idle });
    assert.equal((await w.controller.tick()).phase, "needs-setup");
    await w.controller.tick();
    w.clock.ms += 60_000;
    await w.controller.tick();
    assert.equal(w.calls.length, 1);
});

test("two checks at once start one reconnect, and a person's own action is never raced by one", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const w = world({ statuses: [lost()], helper: async () => { await gate; return reconnected; } });
    const both = Promise.all([w.controller.tick(), w.controller.tick()]);
    release();
    await both;
    assert.equal(w.calls.length, 1);
});

test("the person's own setup still runs the normal approval, and clears a problem that was waiting for them", async () => {
    const w = world({ statuses: [lost()], helper: (args) => (args[0] === "reconnect" ? fail("identity-changed") : ok) });
    assert.equal((await w.controller.tick()).phase, "attention");
    const done = await w.controller.setup(CTX);
    assert.equal(done.ok, true);
    assert.deepEqual(plain(w.calls.map((c) => c.args)), [["reconnect"], ["authorize", CTX]]);
});

test("the page never sees a token, whatever the helper prints", async () => {
    const w = world({ statuses: [lost()], helper: JSON.stringify({ ok: true, command: "reconnect", state: "connected", context: CTX, token: TOKEN }) });
    await w.controller.tick();
    assert.equal(JSON.stringify(w.changes).includes(TOKEN), false);
    assert.equal(JSON.stringify(w.controller.snapshot()).includes(TOKEN), false);
});

test("the wording for the new states is specific and never generic", () => {
    for (const state of ["identity-changed", "busy"]) {
        assert.notEqual(A.messageFor(state), A.messageFor("error"));
    }
    assert.match(A.messageFor("identity-changed"), /approve/i);
});

test("a person's own setup or removal is refused while a reconnect is running, so the two never race", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const w = world({ statuses: [lost()], helper: async (args) => { if (args[0] === "reconnect") await gate; return reconnected; } });
    const running = w.controller.tick();
    while (w.calls.length === 0) await new Promise((resolve) => setTimeout(resolve, 0)); // the reconnect is under way

    const setup = await w.controller.setup(CTX);
    const removal = await w.controller.remove(CTX);
    release();
    await running;

    assert.equal(setup.ok, false);
    assert.equal(removal.ok, false);
    assert.deepEqual(plain(w.calls.map((c) => c.args)), [["reconnect"]], "no authorize or revoke may start beside it");
});
