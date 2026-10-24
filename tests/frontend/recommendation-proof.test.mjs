// The "Prove it on a test cluster" panel, against the application's real template markup in a tree-faithful DOM (support/mini-dom.mjs).
// What these pin: nothing is sent until the person acts; the plan is read before it is approved; one approval starts a run with the
// plan's digest; progress is a count, never a percentage or a guess; a run found again after a reload is drawn from the server's
// record; a lost backend is said so and the last state stays marked stale; abort says "Abort requested — recovering worker" until the
// server's record has ended; nothing is called restored or proven before the record says so. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document, FakeTimers, extractBlock, flush, parseInto, scriptedFetch } from "./support/mini-dom.mjs";

const SCRIPT = readFileSync(new URL("../../app/static/js/recommendation-proof.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../../app/templates/components/recommendations.html", import.meta.url), "utf8");
const SECTION = extractBlock(HTML, /<section[^>]*data-role="rec-proof"/, "</section>");

const BASE = "/api/recommendations/validation-loop";
const CURRENT = `${BASE}/current`;
const TARGETS = `${BASE}/targets`;
const CONTEXT = { namespace: "bookinfo", workflowId: "productpage-view", workflowSource: "FROZEN" };

const TARGET_LIST = { targets: [
    { kind: "DOCKER_DESKTOP", label: "Docker Desktop cluster", available: true, reason: null, identity: { name: "docker-desktop-research", clusterUid: "uid-1", nodes: ["desktop-worker"] } },
    { kind: "DISPOSABLE", label: "Disposable test cluster", available: false, reason: "The disposable test cluster has not been prepared.", identity: null },
], blockingRun: null };

function open(overrides = {}) {
    const doc = new Document();
    parseInto(doc, doc.body, `<div data-role="page">${SECTION}</div>`);
    const sandbox = { window: {}, document: doc, console, Date, Math, JSON, Number, String, Object, Array, Set, Promise };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "recommendation-proof.js" });
    const timers = new FakeTimers();
    const network = scriptedFetch();
    const facts = [];
    const continued = [];
    const root = doc.querySelector('[data-role="rec-proof"]');
    const module = sandbox.window.ResilienceRecommendationProof;
    const panel = module.createProofPanel({
        root, document: doc, fetchImpl: network.impl, getContext: () => CONTEXT, schedule: timers.schedule, cancel: timers.cancel, now: timers.clock,
        onChange: (fact, run) => facts.push([fact, run && run.status]), onContinue: (id) => continued.push(id), ...overrides,
    });
    return { doc, root, panel, timers, network, facts, continued, module, text: () => root.textContent };
}

async function begin(t, current = { run: null }, targets = TARGET_LIST) {
    const started = t.panel.start();
    t.network.respond("GET", CURRENT, 200, current);
    t.network.respond("GET", TARGETS, 200, targets);
    await started;
    await flush();
}

const button = (t, id) => t.root.querySelector(`#${id}`);
const plain = (value) => JSON.parse(JSON.stringify(value));
const press = async (t, id) => { button(t, id).click(); await flush(); };

function run(over = {}) {
    return {
        runKey: "loop-1", status: "RUNNING", statusLabel: "Running", stale: false, heartbeatAgeS: 3,
        target: { kind: "DOCKER_DESKTOP", name: "docker-desktop-research", clusterUid: "uid-1" },
        application: { namespace: "bookinfo", workflowId: "productpage-view", workflowBasis: "FROZEN" }, mode: "QUICK", preliminary: true,
        metrics: { plannedExperiments: 2, finished: 1, skipped: 0, running: 1, candidatesTested: 1, candidatesPlanned: 1, workersCovered: 1, workersPlanned: 2,
            killsUsed: 1, maxFailures: 2, progress: { done: 1, total: 2 } },
        matrix: { workers: ["desktop-worker", "desktop-worker2"], repeatsPerCell: 1, rows: [{ candidateId: "c1", strategy: "TOPOLOGY_SPREAD", service: "reviews", status: "RUNNING",
            cells: [{ worker: "desktop-worker", status: "PASS", repeats: [{ repeat: 1, status: "PASS" }] }, { worker: "desktop-worker2", status: "RUNNING", repeats: [{ repeat: 1, status: "RUNNING" }] }] }] },
        baseline: [], current: { stage: "INJECT_FAILURE", candidateId: "c1", worker: "desktop-worker2", repeat: 1, detail: "stopping desktop-worker2" },
        stages: ["Prepare", "Apply candidate", "Verify rollout", "Inject failure", "Probe", "Recover worker", "Restore deployment"].map((label, i) => ({ id: `S${i}`, label, active: label === "Inject failure" })),
        abort: { available: true, requested: false, label: null },
        safety: { level: "info", message: "A worker is stopped on purpose during each test and restored after it.", workersRestored: "confirmed", deploymentsRestored: "none-changed", recovery: null },
        result: null, stopReason: null, estimate: null, canStartAnother: false, ...over,
    };
}

const PLAN = {
    mode: { mode: "QUICK", candidates: 1, workers: ["desktop-worker", "desktop-worker2"], repeats: 1, measureBaseline: false, plannedFailures: 2, preliminary: true },
    target: { kind: "DOCKER_DESKTOP", name: "docker-desktop-research", clusterUid: "uid-1", apiServer: "https://127.0.0.1:6443", nodes: [] },
    namespace: "bookinfo", workflowId: "productpage-view", workflowBasis: "FROZEN", probe: null,
    candidates: [{ id: "c1", strategy: "TOPOLOGY_SPREAD", service: "reviews", cost: "LOW", rank: 1, yamlSha256: "ab", predictedBlastRadius: 0, riskReduction: 100, predictedPerWorker: {} }],
    skippedCandidates: [], disruption: {}, acknowledgementsNeeded: [], faultReadiness: { smokeOnly: false }, issues: [], canApprove: true, estimate: null,
    riskiestWorker: "desktop-worker", planDigest: "d".repeat(64), notes: ["This mode gives preliminary evidence. Full Research Validation repeats each test for reproducible results."],
};

// ---- starting, and what is sent -----------------------------------------------------------------------------------------------

test("creating the panel sends nothing and schedules nothing", () => {
    const t = open();

    assert.equal(t.network.calls.length, 0);
    assert.equal(t.timers.pending, 0);
});

test("opening the step only reads: the latest run and the clusters", async () => {
    const t = open();
    await begin(t);

    assert.deepEqual(t.network.calls.map((c) => c.method), ["GET", "GET"]);
    assert.ok(t.text().includes("Where") && t.text().includes("Depth") && t.text().includes("Workflow check"));
});

test("both clusters are listed, one that cannot be used is disabled with its reason, and Docker Desktop's effect on the person is said", async () => {
    const t = open();
    await begin(t);

    const radios = t.root.querySelectorAll('input[name="prf-target"]');
    assert.equal(radios.length, 2);
    assert.equal(radios[0].disabled, false);
    assert.equal(radios[1].disabled, true);
    assert.ok(radios[1].parentNode.getAttribute("title").includes("has not been prepared"), "the reason is on the option");
    assert.ok(t.text().includes("workers are stopped on purpose"), "what the chosen cluster means for the person, in one line");
});

test("a cluster that cannot be used is never shown as chosen, and the reasons are said in one line", async () => {
    const t = open();
    await begin(t, { run: null }, { targets: TARGET_LIST.targets.map((x) => ({ ...x, available: false, reason: x.reason || "The cluster's identity could not be read." })), blockingRun: null });

    assert.equal(t.root.querySelectorAll('input[name="prf-target"]').filter((r) => r.checked).length, 0);
    assert.ok(t.text().includes("Docker Desktop: The cluster's identity could not be read."));
    assert.equal(button(t, "btn-prf-plan").disabled, true);
});

test("when only the second cluster is usable it is the one chosen", async () => {
    const t = open();
    await begin(t, { run: null }, { targets: [{ ...TARGET_LIST.targets[0], available: false, reason: "no" }, { ...TARGET_LIST.targets[1], available: true }], blockingRun: null });

    assert.equal(t.root.querySelectorAll('input[name="prf-target"]').find((r) => r.checked).value, "DISPOSABLE");
});

test("the three depths are offered, Quick is chosen, and each says what it runs and whether it is preliminary", async () => {
    const t = open();
    await begin(t);

    const radios = t.root.querySelectorAll('input[name="prf-mode"]');
    assert.deepEqual(plain(radios.map((r) => r.value)), ["SMOKE", "QUICK", "FULL"]);
    assert.equal(radios.find((r) => r.checked).value, "QUICK");
    assert.ok(t.text().includes("best change · every worker · once · preliminary evidence"));
});

test("a running run found at load is shown at once, and no plan form is offered while it is going", async () => {
    const t = open();
    await begin(t, { run: run() });

    assert.ok(t.text().includes("1 of 2 experiments"));
    assert.equal(button(t, "btn-prf-plan"), null);
    assert.deepEqual(plain(t.facts.at(-1)), ["running", "RUNNING"]);
});

// ---- planning and the one approval --------------------------------------------------------------------------------------------

test("the plan is asked for with exactly what the form and the page hold, and nothing else is sent", async () => {
    const t = open();
    await begin(t);

    t.root.querySelector("#prf-probe-url").listeners.input[0]();       // an empty address sends no probe
    await press(t, "btn-prf-plan");

    const sent = t.network.calls.at(-1);
    assert.equal(sent.method, "POST");
    assert.equal(sent.url, `${BASE}/plan`);
    assert.deepEqual(plain(sent.body), { targetKind: "DOCKER_DESKTOP", mode: "QUICK", workflowId: "productpage-view", workflowSource: "FROZEN",
        namespace: "bookinfo", probe: null, measureBaseline: null, smokeWorker: null });
});

test("the plan is drawn from the server's answer, with what stops it and what the approval covers", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, { ...PLAN, canApprove: false, issues: [{ code: "ESSENTIAL_WORKLOAD", blocking: true, message: "Stopping desktop-worker would make kube-system/Deployment/coredns unavailable." }] });
    await flush();

    assert.ok(t.text().includes("Stops the run") && t.text().includes("coredns"));
    assert.equal(button(t, "btn-prf-start").disabled, true, "a blocked plan cannot be started");
    assert.ok(t.text().includes("2 at most") && t.text().includes("every change is put back"));
});

test("with no earlier experiment there is no time estimate, and the page says so instead of promising one", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, PLAN);
    await flush();

    assert.ok(!t.text().includes("Time") && !t.text().toLowerCase().includes("estimate"), "no time row and no promise without history");
    assert.ok(!/\d+ ?min/.test(t.text()));
});

test("Docker Desktop needs its own acknowledgement and every listed workload must be accepted before the run can start", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, { ...PLAN, acknowledgementsNeeded: ["other/Deployment/db"] });
    await flush();

    assert.equal(button(t, "btn-prf-start").disabled, true);
    const [workload, desktop] = t.root.querySelectorAll('input[type="checkbox"]');
    workload.checked = true; workload.listeners.change[0]();
    await flush();
    assert.equal(button(t, "btn-prf-start").disabled, true, "the workload alone is not enough for Docker Desktop");
    const again = t.root.querySelectorAll('input[type="checkbox"]');
    again[1].checked = true; again[1].listeners.change[0]();
    await flush();
    assert.equal(button(t, "btn-prf-start").disabled, false);
});

test("one press starts the run with the plan's digest and the acknowledgements, once, and a second press does nothing", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, { ...PLAN, acknowledgementsNeeded: ["other/Deployment/db"] });
    await flush();
    for (const box of t.root.querySelectorAll('input[type="checkbox"]')) { box.checked = true; box.listeners.change[0](); }
    await flush();

    button(t, "btn-prf-start").click();
    button(t, "btn-prf-start")?.click();
    await flush();

    const starts = t.network.calls.filter((c) => c.url === `${BASE}/runs` && c.method === "POST");
    assert.equal(starts.length, 1);
    assert.equal(starts[0].body.planDigest, "d".repeat(64));
    assert.deepEqual(plain(starts[0].body.acknowledgedWorkloads), ["other/Deployment/db"]);
    assert.equal(starts[0].body.desktopAcknowledged, true);
    t.network.respond("POST", `${BASE}/runs`, 200, run());
    await flush();
    assert.ok(t.text().includes("1 of 2 experiments"));
});

test("a plan that changed since it was reviewed sends the person back to settings with the server's reason", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, PLAN);
    await flush();
    for (const box of t.root.querySelectorAll('input[type="checkbox"]')) { box.checked = true; box.listeners.change[0](); }
    await flush();

    button(t, "btn-prf-start").click();
    await flush();
    t.network.respond("POST", `${BASE}/runs`, 409, { detail: { code: "PLAN_CHANGED", message: "The plan changed since you reviewed it." } });
    await flush();

    assert.ok(t.text().includes("The plan changed since you reviewed it."));
    assert.ok(button(t, "btn-prf-plan"), "back to the settings, to review the new plan");
});

// ---- watching a run -----------------------------------------------------------------------------------------------------------

test("progress is a count of experiments; there is no percentage in the words and no time promise without history", async () => {
    const t = open();
    await begin(t, { run: run() });

    const bar = t.root.querySelector('[role="progressbar"]');
    assert.equal(bar.getAttribute("aria-valuenow"), "1");
    assert.equal(bar.getAttribute("aria-valuemax"), "2");
    assert.ok(!/\d\s?%/.test(t.root.querySelector(".prf-progress__text").textContent));
    assert.ok(!t.text().includes("left"), "no estimate is shown when the server sent none");
});

test("an estimate appears only when the server sends one, with what it rests on", async () => {
    const t = open();
    await begin(t, { run: run({ estimate: { remainingExperiments: 1, estimatedSeconds: 240, basedOnExperiments: 3 } }) });

    assert.ok(t.text().includes("about 4 min left"));
});

test("the matrix shows every cell's state in words as well as marks, and the current operation names the exact stage", async () => {
    const t = open();
    await begin(t, { run: run() });

    const states = t.root.querySelectorAll(".prf-state").map((n) => n.getAttribute("data-status"));
    assert.deepEqual(plain(states), ["PASS", "RUNNING"]);
    assert.ok(t.root.querySelector(".prf-matrix").textContent.includes("Pass") && t.root.querySelector(".prf-matrix").textContent.includes("Running"));
    assert.ok(t.root.querySelector(".prf-op__facts").textContent.includes("desktop-worker2") && t.root.querySelector(".prf-op__now").textContent.includes("stopping desktop-worker2"));
    assert.equal(t.root.querySelectorAll('[aria-current="step"]').length, 1);
    assert.equal(t.root.querySelector('[aria-current="step"]').textContent.includes("Inject failure"), true);
    assert.deepEqual(plain(t.root.querySelectorAll(".prf-track__label").map((n) => n.textContent)), ["Prepare", "Apply candidate", "Verify rollout", "Inject failure", "Probe", "Recover worker", "Restore deployment"]);
});

test("a running run is asked about every two seconds, and polling stops once it has ended", async () => {
    const t = open();
    await begin(t, { run: run() });
    assert.equal(t.timers.pending, 1);

    await t.timers.advance(2000);
    t.network.respond("GET", CURRENT, 200, { run: run({ status: "COMPLETED", statusLabel: "Completed", result: { winner: null, candidates: [], modelMismatches: [], probeFindings: [] } }) });
    await flush();

    assert.equal(t.timers.pending, 0);
    assert.deepEqual(plain(t.facts.at(-1)), ["failed", "COMPLETED"]);
});

test("when the backend cannot be reached the last recorded state stays on screen, marked as possibly out of date, and it is asked again later", async () => {
    const t = open();
    await begin(t, { run: run() });

    await t.timers.advance(2000);
    t.network.fail("GET", CURRENT);
    await flush();

    assert.ok(t.text().includes("The backend cannot be reached") && t.text().includes("may be out of date"));
    assert.ok(t.text().includes("1 of 2 experiments"), "the recorded state is kept");
    assert.equal(t.timers.pending, 1, "it will be asked again, later than before");
});

test("a run that stopped reporting is shown as possibly stopped, not as running", async () => {
    const t = open();
    await begin(t, { run: run({ stale: true, statusLabel: "No update from the run — it may have stopped" }) });

    assert.ok(t.text().includes("may have stopped"));
});

test("a response older than one already shown is dropped", async () => {
    const t = open();
    await begin(t, { run: run() });
    await t.timers.advance(2000);
    const older = t.network.calls.at(-1);
    const newer = t.panel.poll();                                       // a newer request, answered first
    await flush();
    const newest = t.network.calls.at(-1);
    newest.resolve({ ok: true, status: 200, json: async () => ({ run: run({ metrics: { ...run().metrics, finished: 2, progress: { done: 2, total: 2 } } }) }) });
    await newer;
    await flush();
    older.resolve({ ok: true, status: 200, json: async () => ({ run: run() }) });
    await flush();

    assert.ok(t.text().includes("2 of 2 experiments"));
});

// ---- abort and recovery -------------------------------------------------------------------------------------------------------

test("abort asks first, says what it will do, and only then sends", async () => {
    const t = open();
    await begin(t, { run: run() });

    await press(t, "btn-prf-abort");
    assert.equal(t.network.calls.filter((c) => c.url.endsWith("/abort")).length, 0);
    assert.ok(t.text().includes("restarted") && t.text().includes("reverted first"));

    await press(t, "btn-prf-abort-yes");
    assert.equal(t.network.calls.filter((c) => c.url === `${BASE}/runs/loop-1/abort`).length, 1);
});

test("while aborting the page says Abort requested — recovering worker and claims nothing restored beyond the record", async () => {
    const t = open();
    await begin(t, { run: run({ status: "ABORTING", statusLabel: "Abort requested — recovering worker", abort: { available: false, requested: true, label: "Abort requested — recovering worker" },
        safety: { level: "warning", message: "Abort requested — recovering worker. Nothing is claimed restored until it is verified.", workersRestored: "not-confirmed", deploymentsRestored: "not-confirmed", recovery: null } }) });

    assert.ok(t.text().includes("Abort requested — recovering worker"));
    assert.ok(t.text().includes("Finished tests, workers: not yet confirmed") && t.text().includes("Changed"), "deployments are changed, not yet confirmed put back");
    assert.ok(!t.text().includes("restored (verified)"));
    assert.equal(button(t, "btn-prf-abort").disabled, true);
});

test("a run that needs a person is an alert with the command to run and a button to try recovery, which only restores", async () => {
    const t = open();
    await begin(t, { run: run({ status: "NEEDS_MANUAL_RESTORE", statusLabel: "Needs your attention",
        safety: { level: "alert", message: "Something could not be restored.", workersRestored: "not-confirmed", deploymentsRestored: "not-confirmed", actions: ["docker start desktop-worker"], recovery: null } }) });

    assert.equal(t.root.querySelector(".prf-safety").getAttribute("role"), "alert");
    assert.ok(t.text().includes("docker start desktop-worker"));
    assert.deepEqual(plain(t.facts.at(-1)), ["attention", "NEEDS_MANUAL_RESTORE"]);

    await press(t, "btn-prf-recover");
    assert.equal(t.network.calls.at(-1).url, `${BASE}/runs/loop-1/recover`);
});

// ---- the result ---------------------------------------------------------------------------------------------------------------

const PROVEN = () => run({ status: "COMPLETED", statusLabel: "Completed", current: null, canStartAnother: true,
    safety: { level: "ok", message: "Workers and Deployments are confirmed restored.", workersRestored: "confirmed", deploymentsRestored: "confirmed", recovery: null },
    result: { winner: "c1", stopReason: null, killsUsed: 2, candidates: [{ candidateId: "c1", status: "PASSED", reason: null, reverted: true }],
        improvement: { worstObservedBlastRadiusAfter: 0, observedBefore: 100, baselineBasis: "DERIVED_FROM_PLACEMENT", basisNote: "The before-value is derived from the placement, not measured." },
        modelMismatches: [], probeFindings: [] } });

test("a proven change is offered for approval, not applied, and the evidence is called preliminary for a Quick run", async () => {
    const t = open();
    await begin(t, { run: PROVEN() });

    assert.ok(t.text().includes("Proof summary") && t.text().includes("Resilient") && t.text().includes("held on every tested worker"));
    assert.ok(t.text().includes("Preliminary evidence") && t.text().includes("not measured"));
    assert.ok(button(t, "btn-prf-continue").getAttribute("title").includes("Nothing has been applied"));
    assert.deepEqual(plain(t.facts.at(-1)), ["passed", "COMPLETED"]);
    await press(t, "btn-prf-continue");
    assert.deepEqual(plain(t.continued), ["c1"]);
    assert.equal(t.network.calls.filter((c) => c.method === "POST").length, 0);
});

test("a model mismatch is read before the call to action, not after it", async () => {
    const t = open();
    const completed = PROVEN();
    completed.result.modelMismatches = [{ candidateId: "c1", worker: "desktop-worker2", repeat: 1, predictedSurvives: false, readinessHolds: true }];
    await begin(t, { run: completed });

    const text = t.text();
    assert.ok(text.includes("Mismatch") && text.indexOf("Mismatch") < text.indexOf("Continue to approval"), "the mismatch is part of the summary, read before the action");
});

test("while a run is going, verified restoration is said to be about the tests already finished", async () => {
    const t = open();
    await begin(t, { run: run() });

    assert.ok(t.text().includes("Finished tests, workers: restored (verified)"));
});

test("a model mismatch is shown plainly in the result, with both values", async () => {
    const t = open();
    const completed = PROVEN();
    completed.result.modelMismatches = [{ candidateId: "c1", worker: "desktop-worker2", repeat: 1, predictedSurvives: false, readinessHolds: true }];
    await begin(t, { run: completed });

    assert.ok(t.text().includes("Mismatch") && t.text().includes("desktop-worker2: model said fails, readiness held"));
});

test("a probe that disagreed with readiness is reported as a finding about the check, never as a prediction error", async () => {
    const t = open();
    const completed = PROVEN();
    completed.result.probeFindings = [{ candidateId: "c1", worker: "desktop-worker", repeat: 1 }];
    await begin(t, { run: completed });

    assert.ok(t.text().includes("a finding about the check, not a prediction error"));
    assert.ok(!t.text().includes("Prediction wrong"));
});

test("with no winner nothing is offered for approval and nothing is called proven", async () => {
    const t = open();
    const none = PROVEN();
    none.result.winner = null;
    none.result.candidates = [{ candidateId: "c1", status: "INCONCLUSIVE", reason: "1 of 2 planned experiments did not produce a pass.", reverted: true }];
    await begin(t, { run: none });

    assert.ok(t.root.querySelector(".prf-result--none") && !t.root.querySelector(".prf-result--pass"));
    assert.ok(t.text().includes("Not proven") && !t.text().includes("Resilient"));
    assert.equal(button(t, "btn-prf-continue").disabled, true, "there is nothing to approve");
    assert.ok(t.text().includes("did not produce a pass"));
});

test("a finished run offers another plan, and choosing it goes back to the settings without sending anything", async () => {
    const t = open();
    await begin(t, { run: PROVEN() });
    const before = t.network.calls.length;

    await press(t, "btn-prf-again");

    assert.ok(button(t, "btn-prf-plan"));
    assert.equal(t.network.calls.length, before);
});

const SAMPLES = [-10, -5, 0, 5, 10, 15, 20, 25, 30].map((at, i) => ({ at_s: 100 + at, ok: at < 10, status: at < 10 ? 200 : 503, latency_ms: 300 + i * 20, reason: at < 10 ? null : "status 503, expected 200", error: null }));
const MARKS = [{ name: "stop-issued", atS: 100 }, { name: "pods-unready", atS: 112 }, { name: "restore-issued", atS: 135 }, { name: "node-ready", atS: 160 }];

function withResearch() {
    const completed = PROVEN();
    completed.research = { events: [{ event: "RUN_STARTED", detail: "QUICK" }], experiments: [{ candidateId: "c1", worker: "desktop-worker", repeat: 1, status: "PASS", durationS: 61.2,
        outcome: { evidenceLevel: "L2_PROBE", readiness: { holds: true, services: { reviews: { readyElsewhere: 2, readyOnWorker: 0, ok: true }, ratings: { readyElsewhere: 0, readyOnWorker: 0, ok: false } } },
            probe: { workflowSurvives: true, samples: SAMPLES, marks: MARKS }, modelAgrees: true, predictionError: 0 } }] };
    return completed;
}

test("the research data appears only when the server sent it, and never draws a predicted curve", async () => {
    const plain_ = open();
    await begin(plain_, { run: PROVEN() });
    assert.equal(plain_.root.querySelector(".prf-research"), null);

    const detailed = open();
    await begin(detailed, { run: withResearch() });
    const panel = detailed.root.querySelector(".prf-research");
    assert.ok(panel.textContent.includes("Observed during the run") && panel.textContent.includes("none are drawn"));
    assert.ok(!/predicted/i.test(panel.querySelectorAll("figcaption").map((n) => n.textContent).join(" ")), "no chart is labelled predicted");
});

const openTab = async (t, id) => { t.root.querySelector(`[data-prf-tab="${id}"]`).click(); await flush(); };

test("the charts plot the samples that were taken, marked at the stop and the restart, and the table lists them with their result", async () => {
    const t = open();
    await begin(t, { run: withResearch() });

    const charts = t.root.querySelectorAll(".prf-chart");
    assert.equal(charts.length, 2);
    assert.equal(charts[0].querySelectorAll("circle").length, SAMPLES.length);
    assert.ok(charts[0].querySelector("polyline") && charts[0].textContent.includes("Worker stopped") && charts[0].textContent.includes("Worker back"));
    await openTab(t, "samples");
    const rows = t.root.querySelector(".prf-research").querySelectorAll("tr").slice(1);
    assert.equal(rows.length, 8, "the first eight samples; the rest are said to be in the record");
    assert.ok(t.root.querySelector(".prf-research").textContent.includes("1 more samples are in the stored record"));
    assert.ok(rows[7].textContent.includes("status 503, expected 200"));
});

test("readiness and recovery timings are the record's own figures, from the probe's marks", async () => {
    const t = open();
    await begin(t, { run: withResearch() });

    await openTab(t, "readiness");
    const readiness = t.root.querySelector(".prf-research").textContent;
    assert.ok(readiness.includes("reviews: 2 ready elsewhere, 0 on the worker") && readiness.includes("Holds") && readiness.includes("Lost"));
    await openTab(t, "timing");
    const text = t.root.querySelector(".prf-research").textContent;
    assert.ok(text.includes("Seen as down after") && text.includes("12 s") && text.includes("Stopped for") && text.includes("35 s") && text.includes("Back to Ready after") && text.includes("25 s"));
    assert.ok(text.includes("Experiment took") && text.includes("61 s"));
});

test("an experiment with no samples says so instead of drawing an empty chart", async () => {
    const t = open();
    const bare = withResearch();
    bare.research.experiments[0].outcome.probe = { workflowSurvives: null, samples: [], marks: [] };
    await begin(t, { run: bare });

    assert.ok(t.root.querySelector(".prf-research").textContent.includes("No samples were taken"));
    assert.equal(t.root.querySelectorAll(".prf-chart polyline").length, 0);
});

// ---- the disposable cluster: why it is not there, and whether this computer could hold one ----------------------------------------------

test("an unavailable test cluster explains itself and offers a check that only reads", async () => {
    const t = open();
    await begin(t);

    assert.ok(t.text().includes("No test cluster? Check this computer"));
    assert.ok(button(t, "btn-prf-assess"));
    const before = t.network.calls.length;
    await press(t, "btn-prf-assess");
    assert.equal(t.network.calls.length, before + 1);
    assert.deepEqual(plain([t.network.calls.at(-1).method, t.network.calls.at(-1).url]), ["GET", `${BASE}/disposable/assessment`]);
});

test("a machine that cannot hold the cluster is told what to change, and no cluster is offered", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-assess");
    t.network.respond("GET", `${BASE}/disposable/assessment`, 200, { verdict: "INSUFFICIENT", canCreate: false, reasons: ["the nodes alone need 1.9 GiB"],
        advice: ["Raise Docker Desktop's memory to at least 4.4 GiB (Settings → Resources); it is 3.8 GiB now."] });
    await flush();

    assert.ok(t.text().includes("Not enough yet (INSUFFICIENT)") && t.text().includes("Raise Docker Desktop's memory"));
    assert.ok(!t.text().includes("Create it with kind"));
    assert.equal(t.network.calls.filter((c) => c.method === "POST").length, 0);
});

test("even with enough memory the page says the cluster is made on the computer, not from the page", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-assess");
    t.network.respond("GET", `${BASE}/disposable/assessment`, 200, { verdict: "SUFFICIENT", canCreate: true, reasons: [], advice: [] });
    await flush();

    assert.ok(t.text().includes("Enough memory for a test cluster") && t.text().includes("It is not created from this page"));
});

test("a failed check is said, not guessed", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-assess");
    t.network.fail("GET", `${BASE}/disposable/assessment`);
    await flush();

    assert.ok(t.text().includes("The backend cannot be reached"));
});

test("when the test cluster is available the explanation is not shown", async () => {
    const t = open();
    await begin(t, { run: null }, { targets: TARGET_LIST.targets.map((x) => ({ ...x, available: true })), blockingRun: null });

    assert.ok(!t.text().includes("No test cluster? Check this computer"));
});

// ---- the pure parts and the markup --------------------------------------------------------------------------------------------

test("the plan body: Full always carries a baseline, an empty address sends no probe, and the lines of a text box become a list", () => {
    const { planBody, lines } = open().module;
    const form = (over) => ({ targetKind: "DOCKER_DESKTOP", mode: "FULL", baseline: false, smokeWorker: "", probe: { url: " http://127.0.0.1:18080/p ", status: "200", contain: "A\n\n B ", notContain: "" }, ...over });

    assert.equal(planBody(form(), CONTEXT).measureBaseline, true);
    assert.deepEqual(plain(planBody(form(), CONTEXT).probe), { url: "http://127.0.0.1:18080/p", expectedStatus: 200, mustContain: ["A", "B"], mustNotContain: [] });
    assert.equal(planBody(form({ probe: { url: "  ", status: "200", contain: "", notContain: "" } }), CONTEXT).probe, null);
    assert.equal(planBody(form({ mode: "QUICK" }), CONTEXT).measureBaseline, null);
    assert.equal(planBody(form({ mode: "QUICK", baseline: true }), CONTEXT).measureBaseline, true);
    assert.deepEqual(plain(lines("a\n b \n\n")), ["a", "b"]);
});

test("what the stepper is told maps every run state, and a run that needs a person always reads as attention", () => {
    const { proofFact } = open().module;

    assert.equal(proofFact(null), "none");
    assert.equal(proofFact({ status: "RUNNING" }), "running");
    assert.equal(proofFact({ status: "ABORTING" }), "running");
    assert.equal(proofFact({ status: "NEEDS_MANUAL_RESTORE" }), "attention");
    assert.equal(proofFact({ status: "COMPLETED", safety: { level: "alert" }, result: { winner: "c1" } }), "attention");
    assert.equal(proofFact({ status: "COMPLETED", result: { winner: "c1" } }), "passed");
    assert.equal(proofFact({ status: "COMPLETED", result: { winner: null } }), "failed");
    assert.equal(proofFact({ status: "INTERRUPTED" }), "failed");
});

test("the template provides the proof section and, inside the re-rendered page, a slot between the comparison and the approval", () => {
    const pageTemplate = HTML.slice(HTML.indexOf('<template data-role="page-template">'));
    const slot = pageTemplate.indexOf('data-role="rec-proof-slot"');

    assert.ok(SECTION.includes('data-role="rec-proof"') && SECTION.includes("hidden"));
    assert.ok(slot > pageTemplate.indexOf('id="rec-step-3"') && slot < pageTemplate.indexOf('id="rec-step-5"'));
    assert.ok(HTML.indexOf('data-role="rec-proof"') < HTML.indexOf('<div data-role="page-region">'), "the panel is outside the region that is redrawn");
});

test("every script the page needs is loaded, the proof panel before the page script", () => {
    const proof = HTML.indexOf("recommendation-proof.js");
    assert.ok(proof > 0 && proof < HTML.indexOf("components/recommendations.js"));
});

test("no value reaches the page except through textContent: the script never uses innerHTML", () => {
    assert.ok(!/innerHTML|insertAdjacentHTML|document\.write/.test(SCRIPT));
});

// ---- restraint: the step is for deciding, not for reading ---------------------------------------------------------------------------

test("the setup is short: no sentence of explanation is longer than a line, and options hide until opened", async () => {
    const t = open();
    await begin(t);

    const sentences = t.root.querySelectorAll("p").map((n) => n.textContent);
    assert.ok(sentences.every((x) => x.length <= 80), sentences.filter((x) => x.length > 80).join(" | "));
    assert.ok(t.text().length < 700, `setup text is ${t.text().length} characters`);
    assert.ok(t.root.querySelectorAll("details").every((d) => !d.open), "optional parts are closed until the person opens them");
});

test("the plan is a handful of short lines, with the notes behind a disclosure", async () => {
    const t = open();
    await begin(t);
    await press(t, "btn-prf-plan");
    t.network.respond("POST", `${BASE}/plan`, 200, PLAN);
    await flush();

    assert.ok(t.root.querySelectorAll(".prf-summary li").length <= 6);
    assert.ok(t.text().length < 700, `plan text is ${t.text().length} characters`);
    assert.ok(t.root.querySelector("details").textContent.includes("Notes") && !t.root.querySelector("details").open);
});

// ---- the step's own page: a short answer first, the long reading for the research view --------------------------------------------------

test("every state of the step offers a way back to the comparison and on to the approval, and neither is a condition of the other", async () => {
    const setup = open();
    await begin(setup);
    const going = (t) => t.root.querySelectorAll("[data-go]").map((n) => n.getAttribute("data-go"));
    assert.deepEqual(going(setup), ["3", "5"]);

    const running = open();
    await begin(running, { run: run() });
    assert.deepEqual(going(running), ["3", "5"]);

    const done = open();
    await begin(done, { run: PROVEN() });
    assert.deepEqual(going(done), ["3", "5"]);
    assert.ok(done.text().includes("Continue to approval"), "and the proof's own call to action stays");
});

test("the step has the panel's heading, without a number the stepper already gives", async () => {
    const t = open();
    await begin(t);

    const heading = t.root.querySelector("#rec-step-4");
    assert.equal(heading.textContent, "Prove it on a test cluster");
    assert.match(heading.className, /rec-panel__title/);
});

test("the context strip and the four figures are the research reading of a run; the results, the operation and the answer are not", async () => {
    const t = open();
    await begin(t, { run: run() });

    const marked = (selector) => t.root.querySelectorAll(selector).every((n) => /rec-research-only/.test(n.className));
    assert.ok(t.root.querySelectorAll(".prf-strip").length === 1 && marked(".prf-strip"));
    assert.ok(t.root.querySelectorAll(".prf-figures").length === 1 && marked(".prf-figures"));
    for (const selector of [".prf-matrix", ".prf-op", ".prf-safety"]) {
        assert.ok(t.root.querySelectorAll(selector).length >= 1 && t.root.querySelectorAll(selector).every((n) => !/rec-research-only/.test(n.className)), selector);
    }
});

test("a finished run opens with its answer, then the call to action, then the results", async () => {
    const t = open();
    await begin(t, { run: PROVEN() });

    const shown = t.text();
    assert.ok(shown.indexOf("Proof summary") < shown.indexOf("Continue to approval"));
    assert.ok(shown.indexOf("Continue to approval") < shown.indexOf("Results by worker"));
});

test("a finished run does not show an operation card for nothing, and its safety record stands alone", async () => {
    const t = open();
    await begin(t, { run: PROVEN() });

    assert.equal(t.root.querySelectorAll(".prf-op").length, 0);
    assert.match(t.root.querySelector(".prf-opgrid").className, /prf-opgrid--single/);
    assert.equal(t.root.querySelectorAll(".prf-safety").length, 1);
});

test("a running run still shows the operation, with the safety record beside it", async () => {
    const t = open();
    await begin(t, { run: run() });

    assert.equal(t.root.querySelectorAll(".prf-op").length, 1);
    assert.doesNotMatch(t.root.querySelector(".prf-opgrid").className, /single/);
});

test("the proof summary names the change that passed, not its identifier", async () => {
    const t = open();
    await begin(t, { run: PROVEN() });

    const summary = t.root.querySelector(".prf-outcomes").textContent;
    assert.ok(summary.includes("Passed") && summary.includes("Topology spread on reviews") && !summary.includes("c1"), summary);
});

test("the observed data is read one tab at a time, charts first, and a tab is only a view: nothing is fetched by opening one", async () => {
    const t = open();
    await begin(t, { run: withResearch() });
    const before = t.network.calls.length;

    const tabs = t.root.querySelectorAll("[data-prf-tab]");
    assert.deepEqual(tabs.map((n) => n.getAttribute("data-prf-tab")), ["charts", "samples", "readiness", "timing"]);
    assert.deepEqual(tabs.map((n) => n.getAttribute("aria-selected")), ["true", "false", "false", "false"]);
    assert.equal(t.root.querySelectorAll(".prf-chart").length, 2);
    assert.equal(t.root.querySelector(".prf-research").querySelectorAll("table").length, 0, "the samples are not drawn until their tab is open");

    await openTab(t, "samples");
    assert.equal(t.root.querySelectorAll("[data-prf-tab]").filter((n) => n.getAttribute("aria-selected") === "true")[0].getAttribute("data-prf-tab"), "samples");
    assert.equal(t.root.querySelectorAll(".prf-chart").length, 0);
    assert.equal(t.network.calls.length, before);
});
