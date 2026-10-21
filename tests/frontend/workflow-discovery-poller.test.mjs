// Scheduling rules for app/static/js/workflow-discovery-poller.js: when the Workflow dependencies page asks the
// trace service for workflows. A fake clock and stubbed requests stand in for the browser and the network.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/workflow-discovery-poller.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window });
const { create, HEALTH_RETRY_MS, WAITING_FOR_TRACES_MS, EVIDENCE_REFRESH_MS, FOCUS_MIN_GAP_MS, MAX_REFRESH_MS, MAX_WAITING_MS } =
    window.WorkflowDiscoveryPoller;

const flush = () => new Promise((resolve) => setImmediate(resolve));

function harness({ healthy = true, report = { state: "discovered", workflows: [{}] }, hidden = false } = {}) {
    const h = {
        clock: 0,
        timers: [],
        hidden,
        healthy,
        report,
        healthCalls: 0,
        discoverCalls: 0,
        waiting: 0,
        reports: [],
        errors: [],
        discoverGate: null, // a promise the next discovery waits for
    };
    h.poller = create({
        checkHealth: async () => {
            h.healthCalls += 1;
            if (h.healthy instanceof Error) throw h.healthy;
            return h.healthy;
        },
        discover: async () => {
            h.discoverCalls += 1;
            if (h.discoverGate) await h.discoverGate;
            if (h.report instanceof Error) throw h.report;
            return h.report;
        },
        onWaiting: () => { h.waiting += 1; },
        onReport: (r) => h.reports.push(r),
        onError: (e) => h.errors.push(e),
        isHidden: () => h.hidden,
        now: () => h.clock,
        setTimer: (fn, ms) => { const t = { fn, at: h.clock + ms, ms }; h.timers.push(t); return t; },
        clearTimer: (t) => { h.timers = h.timers.filter((x) => x !== t); },
    });
    h.advance = async (ms) => {
        const end = h.clock + ms;
        for (;;) {
            const due = h.timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at)[0];
            if (!due) break;
            h.timers = h.timers.filter((t) => t !== due);
            h.clock = due.at;
            due.fn();
            await flush();
        }
        h.clock = end;
    };
    return h;
}

test("an unreachable trace service shows the waiting state and retries only the health check every 10 seconds", async () => {
    const h = harness({ healthy: false });
    h.poller.start();
    await flush();
    assert.equal(h.waiting, 1);
    assert.equal(h.healthCalls, 1);

    await h.advance(HEALTH_RETRY_MS - 1);
    assert.equal(h.healthCalls, 1, "no retry before 10 seconds");
    await h.advance(1);
    assert.equal(h.healthCalls, 2);
    await h.advance(HEALTH_RETRY_MS);
    assert.equal(h.healthCalls, 3);
    assert.equal(h.discoverCalls, 0, "discovery is not attempted while the service is unreachable");
    assert.equal(HEALTH_RETRY_MS, 10000);
});

test("a health check that throws counts as unreachable, not as a crash", async () => {
    const h = harness({ healthy: new Error("network down") });
    h.poller.start();
    await flush();
    assert.equal(h.waiting, 1);
    await h.advance(HEALTH_RETRY_MS);
    assert.equal(h.healthCalls, 2);
});

test("when the trace service comes up discovery runs by itself, with no reload or click", async () => {
    const h = harness({ healthy: false });
    h.poller.start();
    await flush();
    h.healthy = true;
    await h.advance(HEALTH_RETRY_MS);

    assert.equal(h.discoverCalls, 1);
    assert.equal(h.reports.length, 1);
    assert.equal(h.reports[0].state, "discovered");
});

test("connected with zero traces keeps checking every 10 seconds, then slows once workflows appear", async () => {
    const h = harness({ report: { state: "no_traces", workflows: [] } });
    h.poller.start();
    await flush();
    assert.equal(h.discoverCalls, 1);

    await h.advance(WAITING_FOR_TRACES_MS);
    assert.equal(h.discoverCalls, 2, "still waiting for traces: checked again after 10 seconds");

    h.report = { state: "discovered", workflows: [{}] };
    await h.advance(WAITING_FOR_TRACES_MS);
    assert.equal(h.discoverCalls, 3);
    assert.equal(h.reports.at(-1).state, "discovered");

    await h.advance(EVIDENCE_REFRESH_MS - 1);
    assert.equal(h.discoverCalls, 3, "a controlled refresh, not a tight loop");
    await h.advance(1);
    assert.equal(h.discoverCalls, 4);
});

test("incomplete tracing evidence is polled like waiting, and reported as its own state", async () => {
    const h = harness({ report: { state: "incomplete_traces", workflows: [{}] } });
    h.poller.start();
    await flush();
    assert.equal(h.reports[0].state, "incomplete_traces");
    await h.advance(WAITING_FOR_TRACES_MS);
    assert.equal(h.discoverCalls, 2);
});

test("a report that says the trace service is unavailable goes back to the health retry cadence", async () => {
    const h = harness({ report: { state: "jaeger_unavailable", workflows: [] } });
    h.poller.start();
    await flush();
    await h.advance(HEALTH_RETRY_MS);
    assert.equal(h.healthCalls, 2);
});

test("a failed discovery is reported and retried after 10 seconds", async () => {
    const h = harness({ report: new Error("boom") });
    h.poller.start();
    await flush();
    assert.equal(h.errors.length, 1);
    assert.equal(h.reports.length, 0);
    h.report = { state: "discovered", workflows: [{}] };
    await h.advance(HEALTH_RETRY_MS);
    assert.equal(h.reports.length, 1);
});

test("requests never overlap: refreshes and focus events during a cycle coalesce into one follow-up", async () => {
    const h = harness();
    let release;
    h.discoverGate = new Promise((resolve) => { release = resolve; });
    h.poller.start();
    await flush();
    assert.equal(h.discoverCalls, 1, "the slow discovery is in flight");

    h.poller.refresh();
    h.poller.refresh();
    h.poller.pageVisibilityChanged();
    h.poller.refresh();
    await flush();
    assert.equal(h.discoverCalls, 1, "nothing new starts while one is in flight");

    h.discoverGate = null;
    release();
    await flush();
    await flush();
    assert.equal(h.discoverCalls, 2, "exactly one follow-up for the repeated refreshes");
    assert.equal(h.healthCalls, 2);
});

test("repeated refresh clicks on an idle page each run, one at a time", async () => {
    const h = harness();
    h.poller.start();
    await flush();
    h.poller.refresh();
    await flush();
    h.poller.refresh();
    await flush();
    assert.equal(h.discoverCalls, 3);
    assert.equal(h.timers.length, 1, "only one next cycle is ever scheduled");
});

test("nothing runs or is scheduled while the page is hidden, and becoming visible starts a cycle", async () => {
    const h = harness({ hidden: true });
    h.poller.start();
    await flush();
    assert.equal(h.healthCalls, 0);
    assert.equal(h.timers.length, 0);

    h.hidden = false;
    h.poller.pageVisibilityChanged();
    await flush();
    assert.equal(h.healthCalls, 1);
    assert.equal(h.discoverCalls, 1);
});

test("hiding the tab cancels the pending retry and no request is made for as long as it stays hidden", async () => {
    const h = harness({ healthy: false });
    h.poller.start();
    await flush();
    assert.equal(h.timers.length, 1);

    h.hidden = true;
    h.poller.pageVisibilityChanged();
    assert.equal(h.timers.length, 0);
    await h.advance(10 * 60 * 1000);
    assert.equal(h.healthCalls, 1);

    h.hidden = false;
    h.healthy = true;
    h.poller.pageVisibilityChanged();
    await flush();
    assert.equal(h.healthCalls, 2);
    assert.equal(h.discoverCalls, 1);
});

test("a cycle that finishes after the tab was hidden does not schedule another", async () => {
    const h = harness();
    let release;
    h.discoverGate = new Promise((resolve) => { release = resolve; });
    h.poller.start();
    await flush();
    h.hidden = true;
    h.poller.pageVisibilityChanged();
    release();
    await flush();
    await flush();
    assert.equal(h.timers.length, 0);
});

test("returning to the tab refreshes, but not more often than every 5 seconds", async () => {
    const h = harness();
    h.poller.start();
    await flush();
    assert.equal(h.discoverCalls, 1);

    h.clock += FOCUS_MIN_GAP_MS - 1;
    h.poller.pageVisibilityChanged();
    await flush();
    assert.equal(h.discoverCalls, 1, "focus right after a refresh is ignored");
    assert.equal(h.timers.length, 1, "the regular schedule is still in place");

    h.clock += 1;
    h.poller.pageVisibilityChanged();
    await flush();
    assert.equal(h.discoverCalls, 2);
});

test("stop discards a response that arrives afterwards and schedules nothing", async () => {
    const h = harness();
    let release;
    h.discoverGate = new Promise((resolve) => { release = resolve; });
    h.poller.start();
    await flush();
    h.poller.stop();
    release();
    await flush();
    await flush();
    assert.equal(h.reports.length, 0);
    assert.equal(h.timers.length, 0);
    assert.equal(h.poller.isRunning(), false);
});

test("starting twice does not start two loops", async () => {
    const h = harness();
    h.poller.start();
    h.poller.start();
    await flush();
    assert.equal(h.discoverCalls, 1);
    assert.equal(h.timers.length, 1);
});

test("a stop and start that overlap an in-flight cycle still ends with one running loop", async () => {
    const h = harness();
    let release;
    h.discoverGate = new Promise((resolve) => { release = resolve; });
    h.poller.start();
    await flush();
    h.poller.stop();
    h.poller.start();
    h.discoverGate = null;
    release();
    await flush();
    await flush();
    assert.equal(h.discoverCalls, 2, "the restart's cycle ran once the old one was out of the way");
    assert.equal(h.timers.length, 1);
});

test("an unchanged answer is read less and less often, up to a ceiling", async () => {
    const h = harness({ report: { state: "discovered", fingerprint: "same", workflows: [{}] } });
    h.poller.start();
    await flush();
    assert.equal(h.discoverCalls, 1);

    // 30 s after the first answer (nothing to compare it with), then 60 s, then 120 s, then the ceiling.
    const waits = [];
    for (let i = 0; i < 5; i += 1) {
        const before = h.discoverCalls;
        const timer = h.timers[0];
        waits.push(timer.ms);
        await h.advance(timer.ms);
        assert.equal(h.discoverCalls, before + 1);
    }
    assert.deepEqual(waits, [EVIDENCE_REFRESH_MS, EVIDENCE_REFRESH_MS * 2, MAX_REFRESH_MS, MAX_REFRESH_MS, MAX_REFRESH_MS]);
});

test("a changed answer, or an explicit refresh, goes back to the base cadence", async () => {
    const h = harness({ report: { state: "discovered", fingerprint: "a", workflows: [{}] } });
    h.poller.start();
    await flush();
    await h.advance(EVIDENCE_REFRESH_MS);
    await h.advance(EVIDENCE_REFRESH_MS * 2);
    assert.equal(h.timers[0].ms, MAX_REFRESH_MS, "backed off");

    h.report = { state: "discovered", fingerprint: "b", workflows: [{}] };
    await h.advance(MAX_REFRESH_MS);
    assert.equal(h.timers[0].ms, EVIDENCE_REFRESH_MS, "new workflows: base cadence again");

    await h.advance(EVIDENCE_REFRESH_MS);
    assert.equal(h.timers[0].ms, EVIDENCE_REFRESH_MS * 2, "backing off again");
    h.poller.refresh();
    await flush();
    assert.equal(h.timers[0].ms, EVIDENCE_REFRESH_MS * 2, "refresh re-read it and its answer was the same");
    h.report = { state: "discovered", fingerprint: "c", workflows: [{}] };
    h.poller.refresh();
    await flush();
    assert.equal(h.timers[0].ms, EVIDENCE_REFRESH_MS);
});

test("waiting for traces backs off to a lower ceiling than a discovered refresh", async () => {
    const h = harness({ report: { state: "no_traces", fingerprint: "none", workflows: [] } });
    h.poller.start();
    await flush();
    const waits = [];
    for (let i = 0; i < 5; i += 1) {
        const timer = h.timers[0];
        waits.push(timer.ms);
        await h.advance(timer.ms);
    }
    assert.deepEqual(waits, [WAITING_FOR_TRACES_MS, WAITING_FOR_TRACES_MS * 2, WAITING_FOR_TRACES_MS * 4, MAX_WAITING_MS, MAX_WAITING_MS]);
});

test("reports without a fingerprint never back off", async () => {
    const h = harness();
    h.poller.start();
    await flush();
    for (let i = 0; i < 3; i += 1) {
        assert.equal(h.timers[0].ms, EVIDENCE_REFRESH_MS);
        await h.advance(EVIDENCE_REFRESH_MS);
    }
});
