// Logic tests for app/static/js/recommendation-render.js. Run with: node --test tests/frontend/
// The components render only what they are given. Every kind of missing evidence has an explicit state and
// nothing is estimated, defaulted or derived in the browser (in particular, no client-side "simulation").
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-render.js", import.meta.url), "utf8");

function load() {
    const window = {};
    vm.runInNewContext(source, { window, console });
    return window.ResilienceRecommendations;
}

const view = load();

// Values built inside the vm context carry that realm's prototypes; compare them by content.
const plain = (value) => JSON.parse(JSON.stringify(value));

// ---- a minimal DOM ---------------------------------------------------------------------------------------------

function makeDocument() {
    const create = (tag) => ({
        tag, children: [], attributes: {}, className: "", textContent: "", style: {}, listeners: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        append(...nodes) { this.children.push(...nodes); },
        appendChild(node) { this.children.push(node); return node; },
        replaceChildren(...nodes) { this.children = [...nodes]; },
        addEventListener(type, fn) { this.listeners[type] = fn; },
    });
    return { createElement: create, container: () => create("div") };
}

function walk(node, visit) {
    visit(node);
    (node.children || []).forEach((child) => walk(child, visit));
}

function text(node) {
    const parts = [];
    walk(node, (n) => { if (n.textContent) parts.push(n.textContent); });
    return parts.join(" | ");
}

function find(node, predicate) {
    const out = [];
    walk(node, (n) => { if (predicate(n)) out.push(n); });
    return out;
}

const candidate = (overrides) => ({
    id: "rec-1", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true,
    predictedBlastRadius: 30, riskReduction: 40, resourceCost: "Low", confidence: 0.85, score: 0.9, ...overrides,
});

const report = (...candidates) => ({
    workflowId: "wf", recommendations: candidates, selectedRecommendationId: candidates[0]?.id ?? null,
});

const options = () => ({ document: makeDocument() });

// ---- ranking --------------------------------------------------------------------------------------------------------

test("feasible candidates are ranked by score, infeasible ones are listed after and unranked", () => {
    const ranked = view.rankCandidates(report(
        candidate({ id: "low", score: 0.2 }),
        candidate({ id: "bad", score: 0.99, feasible: false }),
        candidate({ id: "high", score: 0.8 }),
    ));

    assert.deepEqual(plain(ranked.map((r) => [r.candidate.id, r.rank])), [["high", 1], ["low", 2], ["bad", null]]);
});

test("ties keep the order the engine reported", () => {
    const ranked = view.rankCandidates(report(candidate({ id: "a", score: 0.5 }), candidate({ id: "b", score: 0.5 })));
    assert.deepEqual(plain(ranked.map((r) => r.candidate.id)), ["a", "b"]);
});

test("the engine's selected recommendation is flagged and ranking never changes the selection", () => {
    const r = report(candidate({ id: "a", score: 0.1 }), candidate({ id: "b", score: 0.9 }));
    r.selectedRecommendationId = "a";

    const ranked = view.rankCandidates(r);

    assert.deepEqual(plain(ranked.map((x) => [x.candidate.id, x.selected])), [["b", false], ["a", true]]);
});

test("an empty report ranks nothing", () => {
    assert.deepEqual([...view.rankCandidates(report())], []);
    assert.deepEqual([...view.rankCandidates(null)], []);
});

// ---- summary ----------------------------------------------------------------------------------------------------------

test("the baseline is unknown unless evidence supplies it, and is never derived from candidate fields", () => {
    const summary = view.summarise(report(candidate({ predictedBlastRadius: 30, riskReduction: 40 })), {});
    assert.equal(summary.baseline, null);

    const withBaseline = view.summarise(report(candidate()), { baselineBlastRadius: 70 });
    assert.equal(withBaseline.baseline, 70);
});

test("the best predicted blast radius ignores candidates that cannot be applied", () => {
    const summary = view.summarise(report(
        candidate({ id: "ok", predictedBlastRadius: 40 }),
        candidate({ id: "no", predictedBlastRadius: 5, feasible: false }),
    ), {});

    assert.equal(summary.bestPredicted, 40);
    assert.equal(summary.feasibleCount, 1);
    assert.equal(summary.total, 2);
});

test("with no feasible candidate there is no best prediction", () => {
    const summary = view.summarise(report(candidate({ feasible: false })), {});
    assert.equal(summary.bestPredicted, null);
    assert.equal(summary.feasibleCount, 0);
});

// ---- candidate cards ----------------------------------------------------------------------------------------------------

test("a card shows rank, strategy, target, feasibility, selection and every reported figure", () => {
    const o = options(); const c = o.document.container();

    view.renderCandidateCards(c, report(candidate()), { baselineBlastRadius: 70 }, o);

    const shown = text(c);
    for (const expected of ["#1", "Topology Spread", "Target: payment", "Feasible", "Selected Recommendation",
        "70%", "30%", "40 pp", "Low", "85%", "90%"]) {
        assert.ok(shown.includes(expected), `${expected} missing from: ${shown}`);
    }
});

test("an infeasible candidate says so and has no rank", () => {
    const o = options(); const c = o.document.container();

    view.renderCandidateCards(c, report(candidate({ feasible: false })), {}, o);

    assert.ok(text(c).includes("Not Feasible"));
    assert.ok(!text(c).includes("#1"));
});

test("a missing baseline is stated on the card instead of being shown as a number", () => {
    const o = options(); const c = o.document.container();

    view.renderCandidateCards(c, report(candidate()), {}, o);

    assert.ok(text(c).includes("Baseline not provided"));
    assert.ok(!text(c).includes("70%"));
});

test("no candidates is an explicit empty state", () => {
    const o = options(); const c = o.document.container();

    view.renderCandidateCards(c, report(), {}, o);

    assert.ok(text(c).includes("No recommendation candidates"));
    assert.equal(find(c, (n) => /candidate-card\b/.test(n.className)).length, 0);
});

test("hostile text from the API stays text", () => {
    const o = options(); const c = o.document.container();
    const hostile = "<img src=x onerror=alert(1)>";

    view.renderCandidateCards(c, report(candidate({ service: hostile })), {}, o);

    assert.ok(text(c).includes(hostile));
    walk(c, (n) => assert.equal("innerHTML" in n, false));
});

// ---- ranking table ---------------------------------------------------------------------------------------------------------

test("the ranking table has one row per candidate in rank order, with all seven reported columns", () => {
    const o = options(); const body = o.document.container();

    view.renderRankingTable(body, report(candidate({ id: "low", score: 0.2 }), candidate({ id: "high", score: 0.8 })), o);

    const rows = body.children;
    assert.equal(rows.length, 2);
    assert.ok(text(rows[0]).includes("80%") && text(rows[1]).includes("20%"));
    assert.equal(rows[0].children.length, 8); // rank + the seven fields
});

test("an empty ranking table says there is nothing to rank", () => {
    const o = options(); const body = o.document.container();

    view.renderRankingTable(body, report(), o);

    assert.ok(text(body).includes("No candidates"));
});

// ---- validation ----------------------------------------------------------------------------------------------------------------

test("with no validation evidence the panel says so and shows no numbers", () => {
    const o = options(); const c = o.document.container();

    view.renderValidation(c, null, o);

    assert.ok(text(c).includes("No post-deployment validation evidence"));
    assert.doesNotMatch(text(c), /\d+%/);
});

test("validation evidence shows predicted versus observed and the verdict", () => {
    const o = options(); const c = o.document.container();

    view.renderValidation(c, { recommendationId: "rec-1", predictedBlastRadius: 30, observedBlastRadius: 35, predictionError: 5, validated: true }, o);

    for (const expected of ["30%", "35%", "differs from the prediction by 5 percentage points", "Validation Passed"]) {
        assert.ok(text(c).includes(expected), expected);
    }
});

test("a failed validation is labelled failed", () => {
    const o = options(); const c = o.document.container();

    view.renderValidation(c, { predictedBlastRadius: 30, observedBlastRadius: 80, predictionError: 50, validated: false }, o);

    assert.ok(text(c).includes("Validation Failed"));
});

test("validation without an observed value does not pretend one exists", () => {
    const o = options(); const c = o.document.container();

    view.renderValidation(c, { predictedBlastRadius: 30, observedBlastRadius: null, predictionError: null, validated: false }, o);

    assert.ok(text(c).includes("Not observed yet"));
    assert.ok(!text(c).includes("Validation Passed"));
});

// ---- YAML ---------------------------------------------------------------------------------------------------------------------------

test("no YAML proposal is an explicit state that restates the approval rule", () => {
    const o = options(); const c = o.document.container();

    view.renderYaml(c, null, o);

    assert.ok(text(c).includes("No Kubernetes YAML proposal has been generated"));
    assert.ok(text(c).includes("never applied automatically"));
    assert.equal(find(c, (n) => n.tag === "pre").length, 0);
});

test("a YAML proposal is shown verbatim as text with a proposal-only label", () => {
    const o = options(); const c = o.document.container();
    const yaml = "apiVersion: apps/v1\nkind: Deployment # <script>alert(1)</script>";

    view.renderYaml(c, yaml, o);

    const pre = find(c, (n) => n.tag === "pre");
    assert.equal(pre.length, 1);
    assert.equal(pre[0].textContent, yaml);
    assert.ok(text(c).includes("Proposal only"));
});

// ---- placement -----------------------------------------------------------------------------------------------------------------------

const graph = (placements) => ({
    nodes: [], services: [{ name: "payment", pods: placements.map(([name, node]) => ({ name, node })) }],
});

test("with no evidence both sides of the placement view are explicit missing states", () => {
    const o = options(); const current = o.document.container(); const predicted = o.document.container();

    view.renderPlacement(current, predicted, candidate(), {}, o);

    assert.ok(text(current).includes("No topology evidence"));
    assert.ok(text(predicted).includes("No predicted placement"));
});

test("a predicted placement is never derived from the current one", () => {
    const o = options(); const current = o.document.container(); const predicted = o.document.container();
    const context = { currentDeployment: graph([["p-1", "w1"], ["p-2", "w1"]]) };

    view.renderPlacement(current, predicted, candidate(), context, o);

    assert.ok(text(current).includes("p-1") && text(current).includes("w1"));
    assert.ok(text(predicted).includes("No predicted placement"));
    assert.ok(!text(predicted).includes("p-1"));
});

test("engine-supplied simulated placement is shown and labelled as predicted, not observed", () => {
    const o = options(); const current = o.document.container(); const predicted = o.document.container();
    const context = {
        currentDeployment: graph([["p-1", "w1"], ["p-2", "w1"]]),
        simulatedDeployment: graph([["p-1", "w1"], ["p-2", "w2"]]),
    };

    view.renderPlacement(current, predicted, candidate(), context, o);

    assert.ok(text(predicted).includes("w2"));
    assert.ok(text(predicted).includes("Predicted (virtual), not observed"));
});

test("a service absent from the supplied topology is stated, not guessed", () => {
    const o = options(); const current = o.document.container(); const predicted = o.document.container();

    view.renderPlacement(current, predicted, candidate({ service: "elsewhere" }), { currentDeployment: graph([["p-1", "w1"]]) }, o);

    assert.ok(text(current).includes("elsewhere"));
    assert.ok(text(current).includes("not found in the supplied topology"));
});

// ---- hygiene ------------------------------------------------------------------------------------------------------------------------------

test("the module fetches nothing and contains no sample, demo or round-robin logic", () => {
    assert.doesNotMatch(source, /\bfetch\s*\(/);
    assert.doesNotMatch(source, /sample|demo|round-robin|% *nodes\.length|index *% */i);
});
