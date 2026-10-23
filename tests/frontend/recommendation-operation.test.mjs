// The applied-change panel, against the application's real template markup in a tree-faithful DOM (support/mini-dom.mjs).
// What these pin is lifecycle, not wording: handlers are attached once to the elements that were cloned into the page, the panel
// outlives re-rendering and reloads, "Applying change…" is on screen before the request leaves, a second click does nothing, an
// older or late response never replaces a newer one, and a failed request is reconciled with the server before anything can be
// pressed again. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

import { Document, FakeTimers, extractBlock, flush, parseInto, scriptedFetch } from "./support/mini-dom.mjs";

const SCRIPT = readFileSync(new URL("../../app/static/js/recommendation-operation.js", import.meta.url), "utf8");
const HTML = readFileSync(new URL("../../app/templates/components/recommendations.html", import.meta.url), "utf8");
const SECTION = extractBlock(HTML, /<section[^>]*data-role="rec-operation"/, "</section>");
const TEMPLATE = extractBlock(HTML, /<template data-role="operation-template">/, "</template>");

const REC = "rec-live";
const APPLICATION_URL = `/api/recommendations/declared/${REC}/application`;
const CURRENT_URL = "/api/recommendations/applications/current";

function open(overrides = {}) {
    const doc = new Document();
    parseInto(doc, doc.body, `<div data-role="page">${SECTION}${TEMPLATE}</div>`);
    const sandbox = { window: {}, document: doc, console, Date, Math, JSON, Number, String, Object, Array, Set };
    vm.createContext(sandbox);
    vm.runInContext(SCRIPT, sandbox, { filename: "recommendation-operation.js" });
    const timers = new FakeTimers();
    const network = scriptedFetch();
    const seen = [];
    const root = doc.querySelector('[data-role="rec-operation"]');
    const template = doc.querySelector('[data-role="operation-template"]');
    const panel = sandbox.window.ResilienceRecommendationOperation.createOperationPanel({
        root, template, document: doc, fetchImpl: network.impl, schedule: timers.schedule, cancel: timers.cancel, now: timers.clock,
        onStatus: (status) => seen.push(status), ...overrides,
    });
    const find = (role) => root.querySelector(`[data-role="${role}"]`);
    return { doc, root, template, panel, timers, network, seen, find, module: sandbox.window.ResilienceRecommendationOperation };
}

const impact = (blastRadius, over = {}) => ({ blastRadius, survivability: 100 - blastRadius, failedServices: blastRadius ? ["db", "web"] : [], failedWorkflows: [], ...over });
const placement = (byNode) => ({ db: { declaredReplicas: 3, readyReplicas: 3, pods: [], byNode } });

function operation(over = {}) {
    return { direction: "apply", stage: "rollout", stages: ["review", "approved", "applying", "rollout", "analysis", "results"], tone: "busy",
        headline: "Rolling out… waiting for the Deployments to settle on the new generation", nextAction: "wait",
        steps: [{ id: "checking-approval", label: "Checking approval", state: "done", at: "2026-10-23T10:00:00+00:00" },
            { id: "waiting-for-rollout", label: "Waiting for rollout", state: "active", at: null }],
        inProgress: true, pollMs: 3000, connectionLost: false, ...over };
}

function analysis(over = {}) {
    return { analysisId: "analysis-1", applicationId: "apply-1", recommendationId: REC, direction: "apply", status: "PENDING",
        label: "Post-change risk recalculated from live topology. It is a model recalculation over observed pod placement, not an experimentally observed workflow failure rate, and not a controlled-experiment validation.",
        basis: { workflowId: "checkout", workflowBasis: "DECLARED", declaredBy: "asesh", declarationDigest: "ab".repeat(32), failedNode: "w1", failedNodeChosen: "highest blast radius", strategy: "TOPOLOGY_SPREAD", targetServices: ["db"] },
        baseline: { kind: "prediction-from-live-topology", ...impact(100), topologyCapturedAt: "2026-10-23T09:59:00+00:00", placement: placement({ w1: 3 }) },
        predicted: { kind: "virtual-simulation", ...impact(0), placement: placement({ w1: 1, w2: 1, w3: 1 }) },
        observed: null, comparison: null, error: null, ...over };
}

function status(over = {}) {
    const application = { applicationId: "apply-1", recommendationId: REC, approvalId: 7, tier: "research", namespace: "shop", status: "APPLIED", deployments: ["db"],
        yaml: "kind: Deployment\n", yamlSha256: "cd".repeat(32), before: [], cluster: { context: "docker-desktop", apiServer: "https://127.0.0.1:6443", clusterUid: "uid-1" },
        appliedBy: "asesh", appliedAt: "2026-10-23T10:00:00+00:00", resetBy: null, resetAt: null, rollout: null,
        events: [{ at: "2026-10-23T10:00:00+00:00", event: "applying", detail: "by asesh" }], detail: null };
    return { recommendationId: REC, tier: "research", application, rolloutComplete: false, rollout: { direction: "apply", state: "rolling-out", deployments: [] },
        analysis: { apply: analysis() }, operation: operation(), states: { patchesAccepted: true, rolloutCompleted: false, analysisCompleted: false, experimentallyValidated: false,
            experimentNote: "No controlled experiment is run by Apply, Reset or the analysis." },
        canApply: false, canReset: true, canRetryAnalysis: false, writesEnabled: true, reason: null, ...over };
}

const completed = () => status({
    rolloutComplete: true, rollout: { direction: "apply", state: "completed", completedAt: "2026-10-23T10:01:00+00:00", deployments: [] },
    analysis: { apply: analysis({ status: "COMPLETED", observed: { kind: "recalculated-from-live-topology", ...impact(0), topologyCapturedAt: "2026-10-23T10:01:05+00:00", recalculatedAt: "2026-10-23T10:01:06+00:00",
        capturedAfterRollout: true, placement: placement({ w1: 1, w2: 1, w3: 1 }) },
        comparison: { beforeBlastRadius: 100, afterBlastRadius: 0, reductionPoints: 100, direction: "improved", relativeReductionPercent: 100, predictedBlastRadius: 0, predictionDeltaPoints: 0 } }) },
    operation: operation({ stage: "results", tone: "ok", headline: "Risk improved by 100 percentage points: blast radius 100% before, 0% recalculated from the live topology.", nextAction: "reset", inProgress: false, pollMs: null,
        steps: [{ id: "preparing-results", label: "Preparing results", state: "done", at: "2026-10-23T10:01:07+00:00" }] }),
    states: { patchesAccepted: true, rolloutCompleted: true, analysisCompleted: true, experimentallyValidated: false, experimentNote: "No controlled experiment is run by Apply, Reset or the analysis." },
});

const all = (root, tag) => root.querySelectorAll(tag);

// ---- lifecycle of the markup ---------------------------------------------------------------------------------------------------

test("the panel is hidden until there is an operation, and nothing is read or written by merely creating it", () => {
    const t = open();

    assert.equal(t.root.hidden, true);
    assert.equal(t.network.calls.length, 0);
    assert.equal(t.timers.pending, 0);
});

test("the skeleton is cloned into the page once, and handlers sit on the cloned elements, exactly once, across every redraw", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;
    const reset = t.find("op-reset");
    const handlers = () => reset.listeners.click.length;

    assert.ok(reset.isConnected, "the handler is on an element that is in the page");
    assert.notEqual(reset, t.template.content.querySelector('[data-role="op-reset"]'), "not on the template's own copy");
    assert.equal(handlers(), 1);
    for (let i = 0; i < 4; i += 1) {
        await t.timers.advance(3000);
        t.network.respond("GET", APPLICATION_URL, 200, status());
        await flush();
    }
    assert.equal(handlers(), 1, "redrawing must not stack handlers");
    assert.equal(all(t.root, '[data-role="op-body"]').length, 1, "the skeleton was cloned once");
    assert.equal(t.find("op-reset"), reset, "the same element is updated in place");
});

test("every data-role the script reads exists in the real template", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;
    const roles = ["op-badge", "op-stages", "op-headline", "op-progress", "op-message", "op-steps", "op-cards", "op-bars", "op-reduction", "op-placement", "op-label",
        "op-experiment", "op-name", "op-actions", "op-reset", "op-retry", "op-recheck", "op-confirm", "op-confirm-text", "op-confirm-yes", "op-confirm-no", "op-details",
        "op-facts", "op-rollout", "op-events", "op-limits", "op-experiments", "op-yaml", "op-raw", "op-name-wrap"];

    for (const role of roles) assert.ok(t.find(role), `missing ${role}`);
});

test("a panel needs no page region: it survives the page re-rendering everything else", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;
    const page = t.doc.querySelector('[data-role="page"]');
    const region = t.doc.createElement("div");
    page.append(region);

    region.replaceChildren(t.doc.createElement("p"));
    region.replaceChildren();

    assert.equal(t.root.hidden, false);
    assert.ok(t.find("op-headline").textContent.startsWith("Rolling out"));
});

// ---- the first moment ------------------------------------------------------------------------------------------------------------

test("Applying change… is on screen, with the controls off, before the request has left", async () => {
    const t = open();

    const applying = t.panel.startApply(REC, "research", "asesh");

    assert.equal(t.root.hidden, false);
    assert.equal(t.find("op-headline").textContent, "Applying change…");
    assert.equal(t.find("op-progress").hidden, false, "waiting is drawn as indeterminate");
    assert.equal(t.find("op-progress").querySelector('[role="progressbar"]').getAttribute("aria-valuenow"), null, "no invented percentage");
    assert.equal(t.root.getAttribute("aria-busy"), "true");
    assert.equal(t.find("op-reset").disabled, true);
    const current = t.find("op-stages").querySelector('[aria-current="step"]');
    assert.ok(current.textContent.startsWith("Applying"));
    assert.equal(t.network.pending()[0].url, `/api/recommendations/declared/${REC}/apply`);
    assert.deepEqual(t.network.pending()[0].body, { appliedBy: "asesh" });
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 200, status());
    await applying;
});

test("a second click while the first request is in flight sends nothing", async () => {
    const t = open();

    const first = t.panel.startApply(REC, "research", "asesh");
    const second = await t.panel.startApply(REC, "research", "asesh");
    const retry = await t.panel.startApply(REC, "production", "asesh");

    assert.equal(second, false);
    assert.equal(retry, false);
    assert.equal(t.network.count("POST"), 1);
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 200, status());
    await first;
});

test("an apply needs a named person; without one nothing is sent and nothing is shown", async () => {
    const t = open();

    assert.equal(await t.panel.startApply(REC, "research", "  "), false);

    assert.equal(t.network.calls.length, 0);
    assert.equal(t.root.hidden, true);
});

test("while the request is in flight the steps are the ones the server has recorded, and none before it has recorded any", async () => {
    const t = open();
    const applying = t.panel.startApply(REC, "research", "asesh");
    assert.equal(t.find("op-steps").hidden, true, "no step is drawn that the server has not recorded");

    await t.timers.advance(1500);
    const recording = status({ application: { ...status().application, status: "APPLYING" }, operation: operation({
        stage: "applying", headline: "Applying change…", steps: [{ id: "checking-approval", label: "Checking approval", state: "done", at: "2026-10-23T10:00:00+00:00" },
            { id: "saving-snapshot", label: "Saving recovery snapshot", state: "done", at: "2026-10-23T10:00:01+00:00" }, { id: "submitting-changes", label: "Submitting changes", state: "active", at: null }] }) });
    t.network.respond("GET", APPLICATION_URL, 200, recording);
    await flush();

    assert.equal(t.find("op-steps").hidden, false);
    assert.deepEqual(t.find("op-steps").children.map((li) => li.className.split(" ")[1]), ["op-step--done", "op-step--done", "op-step--active"]);
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 200, status());
    await applying;
});

test("an application the server has not recorded yet does not wipe the request that is in flight", async () => {
    const t = open();
    const applying = t.panel.startApply(REC, "research", "asesh");

    await t.timers.advance(1500);
    t.network.respond("GET", APPLICATION_URL, 200, { recommendationId: REC, application: null, operation: operation({ stage: "approved" }) });
    await flush();

    assert.equal(t.root.hidden, false);
    assert.equal(t.find("op-headline").textContent, "Applying change…");
    assert.equal(t.panel.state().status.recommendationId, REC, "the shell that stands for the request is kept");
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 200, status());
    await applying;
});

// ---- ordering ---------------------------------------------------------------------------------------------------------------------

test("a status poll that was in flight when the person acted never replaces what the action produced", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;
    const rolling = status();
    // A poll goes out while the page still believes the rollout is under way …
    t.panel.recheck();
    const slowPoll = t.network.pending().find((c) => c.url === APPLICATION_URL);
    // … the person resets, and the reset's answer comes back first.
    t.find("op-reset").click();
    t.find("op-name").value = "asesh";
    t.find("op-name").dispatchEvent({ type: "input" });
    t.find("op-confirm-yes").click();
    const reset = status({ application: { ...status().application, status: "RESET", resetBy: "asesh" }, canReset: false,
        operation: operation({ direction: "reset", stage: "rollout", headline: "Resetting… waiting for the Deployments to settle on the new generation" }) });
    t.network.respond("POST", `/api/recommendations/declared/${REC}/reset`, 200, reset);
    await flush();
    assert.ok(t.find("op-headline").textContent.startsWith("Resetting"));

    slowPoll.done = true;                              // now the older poll answers, with the state from before the reset
    slowPoll.resolve({ ok: true, status: 200, json: async () => rolling, headers: { get: () => null } });
    await flush();

    assert.ok(t.find("op-headline").textContent.startsWith("Resetting"), t.find("op-headline").textContent);
    assert.equal(t.panel.state().status.application.status, "RESET");
});

test("a response that arrives after the cluster changed is dropped, and the new cluster's operation is read instead", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;
    await t.timers.advance(3000);
    const stale = t.network.pending().find((c) => c.url === APPLICATION_URL);

    const reread = t.panel.clusterChanged();
    assert.equal(t.root.hidden, true, "nothing of the old cluster stays on screen");
    stale.done = true;
    stale.resolve({ ok: true, status: 200, json: async () => status(), headers: { get: () => null } });
    await flush();
    assert.equal(t.root.hidden, true, "the old cluster's late answer was not drawn");
    t.network.respond("GET", CURRENT_URL, 200, { application: null, tier: null });
    await reread;

    assert.equal(t.root.hidden, true);
    assert.equal(t.timers.pending, 0, "nothing keeps polling a cluster that is gone");
});

test("polls never overlap: a tick that fires while one is in flight sends nothing", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;

    await t.timers.advance(3000);
    assert.equal(t.network.count("GET", APPLICATION_URL), 1);
    t.panel.recheck();                               // another poll while the first has not been answered
    await flush();

    assert.equal(t.network.count("GET", APPLICATION_URL), 1, "still the one in flight");
});

// ---- failure is reconciled before anything can be pressed -------------------------------------------------------------------------

test("a request that never returns is reconciled with the server before the controls come back", async () => {
    const t = open();
    const applying = t.panel.startApply(REC, "research", "asesh");
    t.network.fail("POST", `/api/recommendations/declared/${REC}/apply`);
    await flush();

    assert.equal(t.find("op-headline").textContent, "Checking what the server recorded before anything else is allowed…");
    assert.equal(t.find("op-reset").disabled, true);
    t.network.respond("GET", APPLICATION_URL, 200, status());
    await applying;

    assert.equal(t.panel.state().pending, null);
    assert.ok(t.find("op-message").textContent.includes("did not return"), t.find("op-message").textContent);
    assert.ok(t.find("op-message").textContent.includes("The server's record shows"), "what the server recorded is said");
    assert.equal(t.find("op-message").getAttribute("role"), "alert");
    assert.equal(t.find("op-reset").disabled, false);
});

test("a refused apply leaves no panel when the server recorded nothing, and says why", async () => {
    const t = open();
    const applying = t.panel.startApply(REC, "research", "asesh");
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 409, { detail: { code: "APPROVAL_STALE", message: "The topology changed since this was approved." } });
    await flush();
    t.network.respond("GET", APPLICATION_URL, 200, { recommendationId: REC, application: null });
    await applying;

    assert.equal(t.root.hidden, true);
    assert.equal(t.panel.state().pending, null);
    assert.equal(t.panel.state().message.text.startsWith("The topology changed"), true);
});

test("when the status cannot be read after a failure, the person is told the outcome is unknown", async () => {
    const t = open();
    const applying = t.panel.startApply(REC, "research", "asesh");
    t.network.fail("POST", `/api/recommendations/declared/${REC}/apply`);
    await flush();
    t.network.fail("GET", APPLICATION_URL);
    await applying;

    assert.ok(t.panel.state().message.text.includes("may or may not have gone through"));
});

test("an applied change that is later reapplied does not show the old operation's steps as its own", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status({ application: { ...status().application, status: "RESET" }, canReset: false, canApply: true,
        operation: operation({ direction: "reset", stage: "results", tone: "ok", inProgress: false, pollMs: null, steps: [{ id: "x", label: "Restoring the stored state", state: "done", at: null }] }) }));
    await start;
    assert.equal(t.find("op-steps").hidden, false);

    const again = t.panel.startApply(REC, "research", "asesh");
    assert.equal(t.find("op-steps").hidden, true, "the previous operation's steps are not this request's");
    assert.equal(t.find("op-headline").textContent, "Applying change…");
    t.network.respond("POST", `/api/recommendations/declared/${REC}/apply`, 200, status({ application: { ...status().application, applicationId: "apply-2" } }));
    await again;

    assert.equal(t.panel.state().status.application.applicationId, "apply-2");
});

// ---- recovery --------------------------------------------------------------------------------------------------------------------

test("a reload finds the applied change again from the server and never repeats the Apply", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;

    assert.equal(t.root.hidden, false);
    assert.ok(t.find("op-headline").textContent.startsWith("Rolling out"));
    assert.equal(t.panel.state().polling, true, "it resumes watching the rollout");
    assert.equal(t.network.count("POST"), 0);
    await t.timers.advance(3000);
    t.network.respond("GET", APPLICATION_URL, 200, completed());
    await flush();
    assert.equal(t.network.count("POST"), 0);
    assert.ok(t.find("op-headline").textContent.startsWith("Risk improved"));
    assert.equal(t.panel.state().polling, false, "a settled operation is not polled any more");
});

test("the panel is reached by the application the server stored, not by the selected recommendation", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status({ recommendationId: "rec-that-is-no-longer-generated" }));
    await start;

    await t.timers.advance(3000);

    assert.equal(t.network.pending()[0].url, "/api/recommendations/declared/rec-that-is-no-longer-generated/application");
    assert.equal(t.root.hidden, false);
});

test("polling is bounded: it backs off on errors, stops after fifteen minutes, and offers Check again", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;
    let polls = 0;
    for (let minute = 0; minute < 16 * 60 / 6; minute += 1) {
        await t.timers.advance(6000);
        const pending = t.network.pending().find((c) => c.url === APPLICATION_URL);
        if (pending) { polls += 1; pending.done = true; pending.resolve({ ok: true, status: 200, json: async () => status(), headers: { get: () => null } }); await flush(); }
        if (t.timers.pending === 0) break;
    }

    assert.equal(t.timers.pending, 0, "no timer is left running");
    assert.ok(polls < 400, `bounded: ${polls}`);
    assert.equal(t.find("op-recheck").hidden, false, "a person can ask again");
    const before = t.network.count("GET", APPLICATION_URL);
    t.find("op-recheck").click();
    await flush();
    assert.equal(t.network.count("GET", APPLICATION_URL), before + 1);
});

test("a backend that cannot be reached is shown as such and the last recorded state stays on screen", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;

    await t.timers.advance(3000);
    t.network.fail("GET", APPLICATION_URL);
    await flush();

    assert.ok(t.find("op-message").textContent.includes("cannot be reached"));
    assert.ok(t.find("op-headline").textContent.startsWith("Rolling out"), "the last recorded state is still shown");
    assert.equal(t.panel.state().polling, true, "and it will be asked again, later");
});

test("closing the page cancels the timers", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status());
    await start;

    t.panel.destroy();

    assert.equal(t.timers.pending, 0);
});

// ---- the result ------------------------------------------------------------------------------------------------------------------

test("the three-way comparison is drawn from the returned values, in percentage points, with the evidence kind of each", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;

    const cards = t.find("op-cards").children;
    assert.deepEqual(cards.map((c) => c.getAttribute("data-card")), ["before", "predicted", "after"]);
    assert.ok(cards[0].textContent.includes("100%") && cards[0].textContent.includes("Prediction from live topology"));
    assert.ok(cards[1].textContent.includes("0%") && cards[1].textContent.includes("Prediction (virtual simulation)"));
    assert.ok(cards[2].textContent.includes("0%") && cards[2].textContent.includes("Recalculated from live observation"));
    const reduction = t.find("op-reduction").textContent;
    assert.ok(reduction.includes("100 percentage points") && reduction.includes("risk improved"), reduction);
    assert.ok(reduction.includes("Relative reduction (a separate measure): 100%"), reduction);
    const bars = t.find("op-bars").querySelector('[role="img"]');
    assert.equal(bars.getAttribute("aria-label"), "Blast radius: before 100%, predicted 0%, after 0%");
    assert.deepEqual(bars.querySelectorAll(".op-bar-row__fill").map((f) => f.style.width), ["100%", "0%", "0%"]);
    const placementText = t.find("op-placement").textContent;
    assert.ok(placementText.includes("w1 ×3") && placementText.includes("w1 ×1, w2 ×1, w3 ×1"), placementText);
});

test("the result is labelled a recalculation, and nothing on the panel calls it validated", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;

    assert.ok(t.find("op-label").textContent.startsWith("Post-change risk recalculated from live topology"));
    assert.ok(t.find("op-experiment").textContent.includes("No controlled experiment"));
    const everything = t.root.textContent.toLowerCase();
    for (const claim of ["measured resilience improvement", "validated improvement", "experimentally validated"]) {
        assert.ok(!everything.replace("not an experimentally observed", "").includes(claim), claim);
    }
});

test("unknown values stay unknown: no zero, NaN or undefined is drawn in their place", async () => {
    const t = open();
    const body = completed();
    body.analysis.apply.observed = { kind: "recalculated-from-live-topology", blastRadius: null, failedServices: null, topologyCapturedAt: null, placement: { db: null } };
    body.analysis.apply.comparison = { beforeBlastRadius: 100, afterBlastRadius: null, reductionPoints: null, direction: null, relativeReductionPercent: null, predictedBlastRadius: 0, predictionDeltaPoints: null };
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;

    const after = t.find("op-cards").children[2].textContent;
    assert.ok(after.includes("unknown"), after);
    assert.ok(t.find("op-reduction").textContent.includes("not available, because a blast radius is unknown"));
    assert.ok(t.find("op-placement").textContent.includes("Missing evidence"));
    const everything = t.root.textContent;
    assert.ok(!/NaN|undefined|null/.test(everything), everything.match(/.{20}(NaN|undefined|null).{20}/)?.[0]);
});

test("a zero baseline gives no relative reduction rather than an infinite one", async () => {
    const t = open();
    const body = completed();
    body.analysis.apply.comparison = { beforeBlastRadius: 0, afterBlastRadius: 0, reductionPoints: 0, direction: "unchanged", relativeReductionPercent: null, predictedBlastRadius: 0, predictionDeltaPoints: 0 };
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;

    assert.ok(t.find("op-reduction").textContent.includes("not defined, the baseline is 0%"));
    assert.ok(t.find("op-reduction").textContent.includes("risk unchanged"));
});

test("a comparison the server marked unavailable is explained, not drawn as an improvement", async () => {
    const t = open();
    const body = completed();
    body.analysis.apply = analysis({ status: "UNAVAILABLE", error: { code: "WORKFLOW_CHANGED", message: "The declared workflow was edited after this change was applied." } });
    body.operation = operation({ stage: "results", tone: "neutral", headline: "Rollout complete. No comparison is made: The declared workflow was edited after this change was applied.", inProgress: false, pollMs: null, nextAction: "reset" });
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;

    assert.ok(t.find("op-headline").textContent.includes("No comparison is made"));
    assert.ok(t.find("op-cards").children[2].textContent.includes("The declared workflow was edited"));
    assert.equal(t.find("op-reduction").children.length, 0);
    assert.ok(!t.root.textContent.includes("improved"));
});

test("text from the server is never interpreted as markup", async () => {
    const t = open();
    const hostile = '<img src=x onerror="alert(1)"><script>alert(2)</script>';
    const body = completed();
    body.application.deployments = [hostile];
    body.application.appliedBy = hostile;
    body.operation.headline = hostile;
    body.analysis.apply.basis.targetServices = [hostile];
    body.analysis.apply.baseline.placement = { [hostile]: { declaredReplicas: 1, readyReplicas: 1, pods: [], byNode: { [hostile]: 1 } } };
    body.application.events = [{ at: "2026-10-23T10:00:00+00:00", event: hostile, detail: hostile }];
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;
    t.find("op-details").open = true;
    t.find("op-details").dispatchEvent({ type: "toggle" });

    assert.equal(t.root.querySelectorAll("img").length, 0);
    assert.equal(t.root.querySelectorAll("script").length, 0);
    assert.ok(t.find("op-headline").textContent.includes("<img"));
});

test("the technical details carry the identities, timestamps and the rollout evidence, with the raw status behind a second fold", async () => {
    const t = open();
    const body = completed();
    body.rollout.deployments = [{ name: "db", generation: 5, observedGeneration: 5, desired: 3, updated: 3, total: 3, ready: 3, available: 3, state: "settled" }];
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;
    t.find("op-details").open = true;
    t.find("op-details").dispatchEvent({ type: "toggle" });

    const facts = t.find("op-facts").textContent;
    for (const expected of ["apply-1", "rec-live (approval 7)", "docker-desktop", "uid-1", "shop", "checkout", "User declaration by asesh", "w1 (model assumption", "TOPOLOGY_SPREAD",
        "cdcdcdcd", "after the rollout completed: yes"]) {
        assert.ok(facts.includes(expected), `${expected} in ${facts}`);
    }
    assert.ok(t.find("op-rollout").textContent.includes("5 of 5") && t.find("op-rollout").textContent.includes("settled"));
    assert.ok(t.find("op-yaml").textContent.includes("kind: Deployment"));
    assert.ok(JSON.parse(t.find("op-raw").textContent).application.applicationId === "apply-1");
    assert.ok(t.find("op-limits").textContent.includes("declared by a person"));
});

// ---- reset and retry --------------------------------------------------------------------------------------------------------------

test("Reset asks once more, needs a name, shows Resetting… at once, and cannot be sent twice", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;

    t.find("op-reset").click();
    assert.equal(t.find("op-confirm").hidden, false);
    assert.equal(t.doc.activeElement, t.find("op-name"), "focus moves to the name field");
    t.find("op-confirm-yes").click();
    await flush();
    assert.equal(t.network.count("POST"), 0, "no name, nothing sent");
    assert.ok(t.find("op-message").textContent.includes("Enter your name"));

    t.find("op-name").value = "asesh";
    t.find("op-name").dispatchEvent({ type: "input" });
    t.find("op-confirm-yes").click();
    const afterFirst = t.find("op-headline").textContent;
    t.find("op-confirm-yes").click();                 // a second click on the same button
    await flush();

    assert.equal(afterFirst, "Resetting…");
    assert.equal(t.network.count("POST"), 1);
    assert.deepEqual(t.network.pending().find((c) => c.method === "POST").body, { resetBy: "asesh" });
    assert.equal(t.find("op-reset").disabled, true);
    assert.equal(t.find("op-confirm-yes").disabled, true);
    const reset = status({ application: { ...status().application, status: "RESET", resetBy: "asesh" }, canReset: false, canApply: true,
        operation: operation({ direction: "reset", stage: "rollout", headline: "Resetting… waiting for the Deployments to settle on the new generation" }),
        analysis: { apply: completed().analysis.apply, reset: analysis({ direction: "reset", predicted: { kind: "restoration-target", ...impact(100) } }) } });
    t.network.respond("POST", `/api/recommendations/declared/${REC}/reset`, 200, reset);
    await flush();

    assert.ok(t.find("op-headline").textContent.startsWith("Resetting"));
    assert.equal(t.find("op-reset").hidden, true, "nothing left to reset");
    assert.ok(t.find("op-cards").children[1].textContent.includes("The pre-change baseline"), "a reset has a restoration target, not a prediction");
    assert.ok(t.find("op-stages").querySelector(".op-stage--done") || t.find("op-stages").querySelector('[aria-current="step"]'));
    assert.ok(t.find("op-stages").textContent.includes("Resetting"));
});

test("Cancel puts the question away without sending anything", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;

    t.find("op-reset").click();
    t.find("op-confirm-no").click();

    assert.equal(t.find("op-confirm").hidden, true);
    assert.equal(t.network.count("POST"), 0);
});

test("a failed analysis offers Retry, which posts to the retry route with no body and applies nothing", async () => {
    const t = open();
    const body = completed();
    body.analysis.apply = analysis({ status: "FAILED", error: { code: "TOPOLOGY_UNAVAILABLE", message: "Component 2 deployment topology is unavailable" } });
    body.canRetryAnalysis = true;
    body.operation = operation({ stage: "analysis", tone: "attention", headline: "Rollout complete. The failure-impact recalculation failed: Component 2 deployment topology is unavailable", nextAction: "retry-analysis", inProgress: false, pollMs: null });
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;

    assert.equal(t.find("op-retry").hidden, false);
    assert.ok(t.find("op-headline").textContent.includes("recalculation failed"));
    t.find("op-retry").click();
    assert.equal(t.find("op-headline").textContent, "Retrying the failure-impact analysis…");

    const post = t.network.pending().find((c) => c.method === "POST");
    assert.equal(post.url, `/api/recommendations/declared/${REC}/analysis/retry`);
    assert.equal(post.body, null);
    t.network.respond("POST", post.url, 200, completed());
    await flush();
    assert.equal(t.find("op-retry").hidden, true);
    assert.ok(t.find("op-headline").textContent.startsWith("Risk improved"));
});

test("the production tier uses the production routes", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, status({ tier: "production" }));
    await start;

    await t.timers.advance(3000);

    assert.equal(t.network.pending()[0].url, `/api/recommendations/${REC}/application`);
});

// ---- accessibility ----------------------------------------------------------------------------------------------------------------

test("status changes are announced politely, the stage and states are text as well as colour, and controls are real buttons", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;

    const headline = t.find("op-headline");
    assert.equal(headline.getAttribute("role"), "status");
    assert.equal(headline.getAttribute("aria-live"), "polite");
    const stages = t.find("op-stages").children;
    assert.equal(stages.length, 6);
    assert.ok(stages[5].textContent.includes("(current)") && stages[0].textContent.includes("(done)"));
    assert.equal(stages[5].getAttribute("aria-current"), "step");
    assert.ok(t.find("op-badge").textContent.length > 0, "the tone is a word, not only a colour");
    for (const role of ["op-reset", "op-retry", "op-recheck", "op-confirm-yes", "op-confirm-no"]) assert.equal(t.find(role).tag, "button", role);
    assert.equal(t.find("op-details").tag, "details", "technical details are a native disclosure, operable by keyboard");
});

test("redrawing with the same headline does not rewrite the live region, and never rewrites what is being typed", async () => {
    const t = open();
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, completed());
    await start;
    t.find("op-reset").click();
    t.find("op-name").value = "as";
    t.find("op-name").dispatchEvent({ type: "input" });
    const headline = t.find("op-headline");
    const text = headline.childNodes[0];

    const again = t.panel.recheck();
    await flush();
    t.network.respond("GET", APPLICATION_URL, 200, completed());
    await again;

    assert.equal(headline.childNodes[0], text, "the same text node: the live region was not rewritten");
    assert.equal(t.find("op-name").value, "as");
});

test("controlled experiments are listed in their own section, and their absence is stated, not implied", async () => {
    const t = open();
    const body = completed();
    body.experiments = { available: true, note: "", measurements: [{ experimentId: "exp-1", status: "COMPLETED", validated: true, predictionError: 0, workflowBasis: "DECLARED", finishedAt: "2026-10-23T11:00:00+00:00" }] };
    const start = t.panel.start();
    t.network.respond("GET", CURRENT_URL, 200, body);
    await start;

    assert.ok(t.find("op-experiments").textContent.includes("exp-1: COMPLETED, validated true, prediction error 0 percentage points (conditional on a declared workflow)"));

    const none = open();
    const second = none.panel.start();
    none.network.respond("GET", CURRENT_URL, 200, { ...completed(), experiments: { available: true, measurements: [], note: "" } });
    await second;
    assert.ok(none.find("op-experiments").textContent.includes("none have been run for this recommendation"));

    const unknown = open();
    const third = unknown.panel.start();
    unknown.network.respond("GET", CURRENT_URL, 200, { ...completed(), experiments: { available: false, measurements: [], note: "" } });
    await third;
    assert.ok(unknown.find("op-experiments").textContent.includes("unknown"));
});
