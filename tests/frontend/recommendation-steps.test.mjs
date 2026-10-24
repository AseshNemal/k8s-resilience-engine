// Tests for the stepped recommendations page (Member 4): the failure-impact step, the stepper, the evidence summary and the
// Simple / Research split of the template. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-render.js", import.meta.url), "utf8");
const templateHtml = readFileSync(new URL("../../app/templates/components/recommendations.html", import.meta.url), "utf8");

function load() {
    const window = {};
    vm.runInNewContext(source, { window, console });
    return window.ResilienceRecommendations;
}
const view = load();
const plain = (value) => JSON.parse(JSON.stringify(value));

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
const options = () => ({ document: makeDocument() });
const walk = (node, visit) => { visit(node); (node.children || []).forEach((child) => walk(child, visit)); };
const text = (node) => { const parts = []; walk(node, (n) => { if (n.textContent) parts.push(n.textContent); }); return parts.join(" | "); };
const find = (node, predicate) => { const out = []; walk(node, (n) => { if (predicate(n)) out.push(n); }); return out; };

const detail = (over = {}) => ({
    evidence: { failedNode: "w1", failedNodeChosen: "highest blast radius" },
    baselineImpact: { blastRadius: 100, survivability: 0, failedServices: ["payment", "checkout"], failedWorkflows: ["purchase"] },
    currentDeployment: { services: [{ name: "a" }, { name: "b" }, { name: "c" }, { name: "d" }, { name: "e" }] },
    ...over,
});

// ---- step 2: the failure impact --------------------------------------------------------------------------------------------

test("a failure that stops the workflow says so, with the services lost counted against the application", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, detail(), o);

    const shown = text(c);
    assert.ok(shown.includes("If w1 fails") && shown.includes("The workflow does not survive"));
    assert.ok(shown.includes("2 of 5"), "services lost, out of the services of the application");
    assert.ok(shown.includes("100%") && shown.includes("0%"), "blast radius and survival, as reported");
    assert.ok(shown.includes("payment") && shown.includes("checkout") && shown.includes("Workflow lost: purchase"));
    assert.ok(shown.includes("not measured"), "a prediction is never presented as an observation");
    assert.equal(find(c, (n) => n.attributes["data-state"] === "lost").length, 1);
});

test("a failure the workflow survives is stated as such and lists no service", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, detail({ baselineImpact: { blastRadius: 0, survivability: 100, failedServices: [], failedWorkflows: [] } }), o);

    assert.ok(text(c).includes("The workflow survives") && text(c).includes("No service is lost"));
    assert.equal(find(c, (n) => n.attributes["data-state"] === "survives").length, 1);
});

test("without the engine's impact result nothing is shown or estimated", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, detail({ baselineImpact: null }), o);

    assert.ok(text(c).includes("The failure impact was not reported"));
    assert.ok(!/\d+%/.test(text(c)), "no figure is invented");
    assert.equal(find(c, (n) => /rec-tile\b/.test(n.className)).length, 0);
});

test("only what the predictor produces is shown: no success rate, latency, error rate or cost in money", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, detail(), o);

    for (const term of ["success rate", "latency", "error rate", "$"]) assert.ok(!text(c).toLowerCase().includes(term), term);
});

test("a service name from the API stays text", () => {
    const o = options(); const c = o.document.container();
    const hostile = "<img src=x onerror=alert(1)>";

    view.renderImpactSummary(c, detail({ baselineImpact: { blastRadius: 100, survivability: 0, failedServices: [hostile], failedWorkflows: [] } }), o);

    assert.ok(text(c).includes(hostile));
    walk(c, (n) => assert.equal("innerHTML" in n, false));
});

// ---- the stepper -----------------------------------------------------------------------------------------------------------

const states = (facts) => plain(view.computeSteps(facts)).map((step) => step.state);
const READY = { chosen: true, status: "ready", selected: true, applied: false };

test("nothing chosen: the first step is current and the rest wait", () => {
    assert.deepEqual(states({ chosen: false, status: "loading", selected: false, applied: false }), ["current", "waiting", "waiting", "waiting", "waiting"]);
});

test("while the recommendation is being asked for, the second step is current", () => {
    assert.deepEqual(states({ chosen: true, status: "loading-recommendation" }), ["done", "current", "waiting", "waiting", "waiting"]);
});

test("missing evidence blocks the second step and nothing after it is marked done", () => {
    assert.deepEqual(states({ chosen: true, status: "missing-evidence", selected: true, applied: true }), ["done", "blocked", "waiting", "waiting", "waiting"]);
});

test("a recommendation with no selected candidate blocks the third step", () => {
    assert.deepEqual(states({ chosen: true, status: "ready", selected: false }), ["done", "done", "blocked", "waiting", "waiting"]);
});

test("a selected candidate makes proving the step to do, and the approval waits for it", () => {
    assert.deepEqual(states({ ...READY, proof: "none" }), ["done", "done", "done", "current", "waiting"]);
});

test("the approval is never done or the step to do while step 4 is unproven, even if a change was applied earlier", () => {
    assert.deepEqual(states({ ...READY, applied: true }), ["done", "done", "done", "current", "waiting"]);
    assert.deepEqual(states({ ...READY, applied: true, proof: "failed" }), ["done", "done", "done", "blocked", "waiting"]);
});

test("a proof the server says covers this change completes step 4 and makes the approval the one to do; applying completes it", () => {
    assert.deepEqual(states({ ...READY, proof: "passed" }), ["done", "done", "done", "done", "current"]);
    assert.deepEqual(states({ ...READY, proof: "passed", proven: true }), ["done", "done", "done", "done", "current"]);
    assert.deepEqual(states({ ...READY, proof: "passed", applied: true }), ["done", "done", "done", "done", "done"]);
});

test("a proof of a different change does not count for this one, and the server's answer wins over the run's", () => {
    assert.deepEqual(states({ ...READY, proof: "passed", proven: false }), ["done", "done", "done", "current", "waiting"]);
    assert.deepEqual(states({ ...READY, proof: "none", proven: true }), ["done", "done", "done", "done", "current"]);
});

test("while a proof is running it is the current step and the approval waits; a failed or attention-needing proof is flagged", () => {
    assert.deepEqual(states({ ...READY, proof: "running" }), ["done", "done", "done", "current", "waiting"]);
    assert.deepEqual(states({ ...READY, proof: "failed" }), ["done", "done", "done", "blocked", "waiting"]);
    assert.deepEqual(states({ ...READY, proof: "attention" }), ["done", "done", "done", "blocked", "waiting"]);
});

test("a decision recorded before the change was proven is an earlier approval: kept and shown, but not done and not validation", () => {
    for (const approval of ["PENDING_APPROVAL", "APPROVED", "REJECTED"]) {
        assert.deepEqual(states({ ...READY, approval }), ["done", "done", "done", "current", "recorded"], approval);
    }
    assert.deepEqual(states({ ...READY, approval: "APPROVED", applied: true }), ["done", "done", "done", "current", "recorded"], "applied is not validated");
    assert.deepEqual(states({ ...READY, approval: "APPROVED", proof: "passed" }), ["done", "done", "done", "done", "current"], "once proven it is the step to do again");
    assert.deepEqual(states({ ...READY, approval: "none" }), ["done", "done", "done", "current", "waiting"]);
});

test("a proof is never shown before there is a candidate to prove", () => {
    assert.deepEqual(states({ chosen: true, status: "ready", selected: false, proof: "passed" }), ["done", "done", "blocked", "waiting", "waiting"]);
});

test("every step says whether it can be opened: the approval can be, before it is proven, once there is a candidate", () => {
    const open = (facts) => plain(view.computeSteps(facts)).map((step) => step.available);
    assert.deepEqual(open({ chosen: false }), [true, false, false, false, false]);
    assert.deepEqual(open({ chosen: true, status: "loading" }), [true, true, false, false, false]);
    assert.deepEqual(open({ chosen: true, status: "missing-evidence" }), [true, true, false, false, false]);
    assert.deepEqual(open({ ...READY, selected: false }), [true, true, true, false, false]);
    assert.deepEqual(open({ ...READY, proof: "none" }), [true, true, true, true, true]);
});

test("each step says in words where it stands", () => {
    const captions = (facts) => plain(view.computeSteps(facts)).map((step) => step.caption);
    assert.deepEqual(captions({ ...READY }), ["Done", "Done", "Done", "Not run yet", "After step 4"]);
    assert.deepEqual(captions({ ...READY, proof: "running" }), ["Done", "Done", "Done", "Running", "After step 4"]);
    assert.deepEqual(captions({ ...READY, proof: "passed" }), ["Done", "Done", "Done", "Done", "Ready"]);
    assert.deepEqual(captions({ ...READY, approval: "APPROVED" }), ["Done", "Done", "Done", "Not run yet", "Earlier approval"]);
    assert.deepEqual(captions({ ...READY, proof: "passed", approval: "APPROVED" }), ["Done", "Done", "Done", "Done", "Apply next"]);
    assert.equal(captions({ ...READY, proof: "failed" })[3], "Needs attention");
});

test("the stepper is five buttons that name the state in words and marks, say which step is on screen, and open only what can be opened", () => {
    const o = options(); const c = o.document.container();
    const opened = [];

    view.renderStepper(c, view.computeSteps({ chosen: true, status: "ready", selected: false }), { ...o, viewing: 2, onSelect: (n) => opened.push(n) });

    const items = find(c, (n) => n.tag === "li");
    assert.equal(items.length, 5);
    assert.deepEqual(items.map((n) => n.attributes["data-state"]), ["done", "done", "blocked", "waiting", "waiting"]);
    const buttons = find(c, (n) => n.tag === "button");
    assert.equal(buttons.length, 5);
    assert.deepEqual(buttons.filter((n) => n.attributes["aria-current"] === "step").map((n) => n.attributes["data-step"]), ["2"], "only the step on screen is current for assistive technology");
    assert.deepEqual(buttons.map((n) => !!n.disabled), [false, false, false, true, true]);
    assert.ok(text(items[2]).includes("Needs attention") && text(items[0]).includes("Done") && text(items[3]).includes("Waiting"));
    assert.ok(text(items[0]).includes("✓") && text(items[2]).includes("!") && text(items[3]).includes("4") && text(items[4]).includes("5"));
    buttons[2].listeners.click();
    assert.deepEqual(opened, [3]);
});

test("an earlier approval has its own mark and word, so it cannot be mistaken for a finished step", () => {
    const o = options(); const c = o.document.container();

    view.renderStepper(c, view.computeSteps({ ...READY, approval: "APPROVED" }), { ...o, viewing: 5 });

    const last = find(c, (n) => n.tag === "li")[4];
    assert.equal(last.attributes["data-state"], "recorded");
    assert.ok(text(last).includes("◷") && text(last).includes("Earlier approval") && !text(last).includes("✓"));
});

test("the evidence summary counts what is available and asks for attention when something is missing", () => {
    assert.deepEqual(plain(view.evidenceSummary([{ state: "available" }, { state: "pending" }])), { text: "Evidence: 1 of 2 available", attention: false });
    assert.deepEqual(plain(view.evidenceSummary([{ state: "available" }, { state: "missing" }])), { text: "Evidence: 1 of 2 available", attention: true });
    assert.deepEqual(plain(view.evidenceSummary([{ state: "incomplete" }])), { text: "Evidence: 0 of 1 available", attention: true });
    assert.deepEqual(plain(view.evidenceSummary([])), { text: "Evidence", attention: false });
});

// ---- candidate cards in the plain view ---------------------------------------------------------------------------------

test("a candidate card shows strategy, target, feasibility, the predicted improvement and the cost up front, and keeps the longer text behind Details", () => {
    const o = options(); const c = o.document.container();
    const feasible = { id: "a", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 };
    const blocked = { ...feasible, id: "b", type: "REPLICA_INCREASE", feasible: false, predictedBlastRadius: 100, riskReduction: 0 };

    view.renderCandidateCards(c, { recommendations: [feasible, blocked], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, {
        ...o, descriptions: { a: "Add a spread constraint." }, reasons: { b: "Not enough worker nodes." },
    });

    const cardsDrawn = find(c, (n) => /\bcandidate-card\b/.test(n.className));
    assert.equal(cardsDrawn.length, 2);
    const [first, second] = cardsDrawn.map((n) => text(n));
    for (const expected of ["Topology spread", "on payment", "Recommended", "Feasible", "−100 pp", "Low cost"]) assert.ok(first.includes(expected), expected);
    for (const expected of ["Replica increase", "Not feasible", "No change"]) assert.ok(second.includes(expected), expected);
    const details = find(c, (n) => n.tag === "details");
    assert.equal(details.length, 2, "each card has one disclosure");
    assert.ok(text(details[0]).includes("Add a spread constraint.") && text(details[1]).includes("Not enough worker nodes."));
    assert.ok(text(details[1]).startsWith("Why not feasible"), "the disclosure of a blocked card says what it holds");
    const research = find(c, (n) => /rec-research-only/.test(n.className));
    assert.equal(research.length, 5, "rank, confidence and score on the ranked card; confidence and score on the other");
    assert.ok(research.every((n) => /Rank|Confidence|Score/.test(text(n))));
});

test("the lead card carries the problem, the validation status and nothing more is repeated elsewhere", () => {
    const o = options(); const c = o.document.container();
    const one = { id: "a", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 };
    const two = { ...one, id: "b", type: "REPLICA_INCREASE" };

    view.renderCandidateCards(c, { recommendations: [one, two], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 },
        { ...o, hero: { problem: "If w1 fails, the workflow does not survive.", validationText: "Not proven on a test cluster." } });

    const lead = find(c, (n) => /candidate-card--lead/.test(n.className));
    assert.equal(lead.length, 1, "only the selected card leads");
    assert.ok(text(lead[0]).includes("If w1 fails, the workflow does not survive.") && text(lead[0]).includes("Not proven on a test cluster."));
    assert.equal(find(c, (n) => /candidate-card__validation-text/.test(n.className)).length, 1, "the status can be updated when the server answers");
    const others = find(c, (n) => /\bcandidate-card\b/.test(n.className) && !/lead/.test(n.className));
    assert.ok(!text(others[0]).includes("Problem") && !text(others[0]).includes("Validation"));
});

test("the gauge draws now and predicted, prints both figures, and does not colour a prediction of no gain as a gain", () => {
    const o = options();
    const gain = view.gauge(o.document, 100, 0);
    const none = view.gauge(o.document, 100, 100);
    const rows = (g) => find(g, (n) => /gauge__fill/.test(n.className)).map((n) => n.className);

    assert.ok(text(gain).includes("Now") && text(gain).includes("Predicted") && text(gain).includes("100%") && text(gain).includes("0%"));
    assert.deepEqual(rows(gain), ["gauge__fill gauge__fill--now", "gauge__fill gauge__fill--after"]);
    assert.deepEqual(rows(none), ["gauge__fill gauge__fill--now", "gauge__fill gauge__fill--now"], "no gain is not drawn green");
    assert.equal(find(gain, (n) => /gauge__fill--after/.test(n.className))[0].style.width, "0%");
    assert.match(gain.attributes["aria-label"], /100% now, 0% predicted/);
    assert.match(view.gauge(o.document, null, 30).attributes["aria-label"], /baseline not supplied, 30% predicted/, "a missing baseline is said, not drawn");
});

// ---- the template: every block belongs to a view ------------------------------------------------------------------------

const pageTemplate = templateHtml.slice(templateHtml.indexOf('<template data-role="page-template">'), templateHtml.indexOf("</template>", templateHtml.indexOf('<template data-role="page-template">')));
const researchOnly = [...templateHtml.matchAll(/<section[^>]*rec-research-only[^>]*>[\s\S]*?<\/section>/g)].map((m) => m[0]);
const inResearchOnly = (role) => researchOnly.some((block) => block.includes(`data-role="${role}"`));

test("every block drawn for a recommendation is a step or is marked research-only, so none is shown by accident in either view", () => {
    const sections = [...pageTemplate.matchAll(/<section class="([^"]*)"/g)].map((m) => m[1]);
    assert.equal(sections.length, 3, "the impact, the comparison and the approval; the proof panel is its own, outside the re-rendered region");
    for (const classes of sections) assert.ok(/\brec-panel\b|\brec-research-only\b/.test(classes), classes);
    for (const marker of [...pageTemplate.matchAll(/data-step-panel="(\d)"/g)].map((m) => m[1])) assert.ok(["2", "3", "4", "5"].includes(marker), marker);
});

test("the plain view keeps the steps' own parts and the research view alone keeps the ranking, the reasons and the raw data", () => {
    for (const role of ["rec-impact", "rec-candidate-list", "rec-outcome", "rec-proposal-summary", "approval-state-container", "approval-actions"]) {
        assert.ok(pageTemplate.includes(`data-role="${role}"`), role);
        assert.ok(!inResearchOnly(role), `${role} must be visible in the plain view`);
    }
    for (const role of ["rec-detail-list", "rec-candidate-table-body", "rec-validation", "rec-yaml", "rec-failures", "rec-placement-current", "rec-raw-json"]) {
        assert.ok(inResearchOnly(role), `${role} belongs to the research view`);
    }
});

test("the prediction-not-observation statement stays in the plain view", () => {
    assert.ok(pageTemplate.includes("Predictions from a simulation, not measurements."));
    assert.ok(!researchOnly.some((block) => block.includes("Predictions from a simulation, not measurements.")));
    assert.ok(pageTemplate.includes('data-role="rec-validation-note"') && !inResearchOnly("rec-validation-note"));
});

test("the page explains the explicit Apply and test action", () => {
    assert.ok(!/<button\b[^>]*>[^<]*(?:Approve and Implement|Approve and Apply)/i.test(templateHtml));
    assert.ok(pageTemplate.includes("What happens after Apply") && pageTemplate.includes("Run configured workflow checks"));
    // The extension build adds the one-sentence statement inside the approval card; it must still say that approving applies nothing.
    const build = readFileSync(new URL("../../extension/build_ui.py", import.meta.url), "utf8");
    assert.ok(build.includes("Apply changes the selected Deployments") && build.includes("Reset restores the saved configuration."));
});

test("the approval gate keeps the exact opening tag the extension build patches, once, inside the fifth step", () => {
    const marker = '<div class="card" id="approval-section">';
    assert.equal(templateHtml.split(marker).length - 1, 1);
    const step = pageTemplate.slice(pageTemplate.indexOf('aria-labelledby="rec-step-5"'));
    assert.ok(step.indexOf(marker) > step.indexOf('id="rec-step-5"'), "the marker sits under the step's heading");
});

// ---- the services table, the selected improvement and the card marks ------------------------------------------------------------

const withRoles = () => detail({
    workflowServices: [{ name: "payment", role: "mandatory" }, { name: "cart", role: "mandatory" }, { name: "recommendations", role: "optional" }],
    baselineImpact: { blastRadius: 100, survivability: 0, failedServices: ["payment"], failedWorkflows: ["purchase"] },
});

test("every service of the workflow is listed with its role and whether it is lost, and the figure counts against the workflow", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, withRoles(), o);

    const rows = find(c, (n) => n.tag === "tr" && n.attributes["data-state"]);
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((r) => r.attributes["data-state"]), ["lost", "survives", "survives"]);
    assert.ok(text(rows[0]).includes("payment") && text(rows[0]).includes("Mandatory") && text(rows[0]).includes("Lost"));
    assert.ok(text(rows[2]).includes("Optional") && text(rows[2]).includes("Survives"));
    assert.ok(text(c).includes("1 of 3"), "services lost, out of the workflow's own services");
    assert.ok(text(c).includes("If w1 fails"), "the column says which failure it is");
});

test("a lost service the workflow does not list is still shown, with its role marked as not reported", () => {
    const o = options(); const c = o.document.container();
    const shown = withRoles();
    shown.baselineImpact.failedServices = ["payment", "ledger"];

    view.renderImpactSummary(c, shown, o);

    const ledger = find(c, (n) => n.tag === "tr" && text(n).startsWith("ledger"));
    assert.equal(ledger.length, 1);
    assert.ok(text(ledger[0]).includes("Not reported") && text(ledger[0]).includes("Lost"));
});

test("without the workflow's roles the lost services are still listed, never guessed", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, detail({ workflowServices: null }), o);

    assert.equal(find(c, (n) => n.tag === "table").length, 0);
    assert.ok(text(c).includes("payment") && text(c).includes("checkout"));
});

test("the selected improvement says what it changes and what is predicted, as a prediction", () => {
    const o = options(); const c = o.document.container();
    const chosen = { type: "TOPOLOGY_SPREAD", service: "payment", recommendedChange: "Add a topologySpreadConstraint on kubernetes.io/hostname",
        predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0, riskReduction: 100 }, cost: { resource: "LOW" } };

    view.renderSelectedImprovement(c, chosen, o);

    const shown = text(c);
    assert.ok(shown.includes("Topology spread") && shown.includes("on payment") && shown.includes("topologySpreadConstraint"));
    assert.ok(shown.includes("100%") && shown.includes("0%") && shown.includes("Low cost"));
    assert.ok(shown.includes("Predicted, not observed."));
    assert.equal(find(c, (n) => n.attributes["data-kind"] === "topology_spread").length, 1);
});

test("with nothing selected the approval step says so instead of showing an improvement", () => {
    const o = options(); const c = o.document.container();

    view.renderSelectedImprovement(c, null, o);

    assert.ok(text(c).includes("Nothing to approve"));
    assert.equal(find(c, (n) => /\bgauge\b/.test(n.className)).length, 0);
});

test("only the selected candidate is marked Recommended, and every card carries an icon for its strategy", () => {
    const o = options(); const c = o.document.container();
    const one = { id: "a", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 };
    const two = { ...one, id: "b", type: "POD_ANTI_AFFINITY", feasible: false };

    view.renderCandidateCards(c, { recommendations: [one, two], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, o);

    assert.equal(find(c, (n) => n.textContent === "Recommended").length, 1);
    assert.deepEqual(find(c, (n) => /candidate-card__icon/.test(n.className)).map((n) => n.attributes["data-kind"]).sort(), ["pod_anti_affinity", "topology_spread"]);
});

test("step 1 holds the one action, and the step 4 parts and the proposal's facts are all in the plain view", () => {
    const step1 = templateHtml.slice(templateHtml.indexOf('data-role="rec-context"'), templateHtml.indexOf('data-role="rec-proof"'));
    assert.ok(step1.includes('data-role="reload-button"'), "the reload action lives in step 1");
    assert.ok(!templateHtml.slice(0, templateHtml.indexOf('<ol class="rec-stepper"')).includes('data-role="reload-button"'), "and no longer in the header");
    for (const role of ["rec-selected", "approval-state-container", "approval-actions", "rec-proposal-summary", "rec-validation-note"]) {
        assert.ok(pageTemplate.includes(`data-role="${role}"`), role);
        assert.ok(!inResearchOnly(role), `${role} must be visible in the plain view`);
    }
});

test("the approval dialog is hidden by this page's own stylesheet until the script shows it", () => {
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /#approval-modal\s*\{[^}]*display:\s*none/, "hidden by default (the deployments stylesheet is not loaded on this page)");
    assert.match(css, /#approval-modal\.is-visible\s*\{[^}]*display:\s*flex/, "shown when the script adds .is-visible");
});

// ---- what to do next in step 1 -------------------------------------------------------------------------------------------------

const hintFor = (ctx) => plain(view.nextActionHint(ctx));

test("the hint names the application when it is the application that has to be chosen", () => {
    assert.deepEqual(hintFor({ status: "needs-input", namespaceChoice: "ambiguous", namespace: "", workflows: [] }),
        { field: "namespace", text: "Choose the application to analyse." });
});

test("the hint names the workflow, and says a declared one is never picked for the person", () => {
    const declaredOnly = hintFor({ status: "needs-input", namespace: "shop", workflows: [{ applicable: true, declared: true }] });
    assert.equal(declaredOnly.field, "workflow");
    assert.ok(declaredOnly.text.includes("Declared") && declaredOnly.text.includes("never picked for you") && declaredOnly.text.includes("research"));
    assert.deepEqual(hintFor({ status: "needs-input", namespace: "shop", workflows: [{ applicable: true, declared: false }, { applicable: true, declared: true }] }),
        { field: "workflow", text: "Choose a workflow to continue." });
});

test("a declared-only context is reported by the controller as blocked, and the hint still names the workflow", () => {
    const hint = hintFor({ status: "blocked", namespace: "shop", workflowId: "", workflows: [{ applicable: true, declared: true }] });
    assert.equal(hint.field, "workflow");
    assert.ok(hint.text.includes("never picked for you"));
});

test("an option that cannot be used is not something to choose, and nothing is hinted when nothing is waiting", () => {
    assert.equal(view.nextActionHint({ status: "needs-input", namespace: "shop", workflows: [{ applicable: false, declared: false }] }), null);
    assert.equal(view.nextActionHint({ status: "blocked", namespace: "shop", workflowId: "", workflows: [] }), null, "nothing to choose: the evidence says what is missing");
    assert.equal(view.nextActionHint({ status: "blocked", namespace: "shop", workflowId: "checkout", workflows: [{ applicable: true, declared: false }] }), null, "already chosen");
    for (const status of ["ready", "loading", "loading-recommendation"]) {
        assert.equal(view.nextActionHint({ status, namespace: "shop", workflows: [{ applicable: true, declared: true }] }), null, status);
    }
    assert.equal(view.nextActionHint(null), null);
});

test("the hint line and the highlight exist in step 1 and the highlight is not carried by colour alone", () => {
    const step1 = templateHtml.slice(templateHtml.indexOf('data-role="rec-context"'), templateHtml.indexOf('data-role="rec-proof"'));
    assert.ok(step1.includes('data-role="rec-hint"') && step1.includes('id="rec-next-hint"') && step1.includes('role="status"'));
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /\.rec-inputs__input\[data-attention="true"\]\s*\{[^}]*border-color[^}]*box-shadow/, "a border and a ring");
});

// ---- less text -----------------------------------------------------------------------------------------------------------------

test("a card's long description and reason are behind its disclosure in full, not clamped or truncated", () => {
    const o = options(); const c = o.document.container();
    const long = "Add one replica to the Deployment together with a topologySpreadConstraint on kubernetes.io/hostname, so the added replica runs elsewhere.";
    const one = { id: "a", type: "REPLICA_INCREASE", service: "payment", feasible: false, predictedBlastRadius: 100, riskReduction: 0, resourceCost: "MEDIUM", confidence: 0.9, score: 0 };

    view.renderCandidateCards(c, { recommendations: [one], selectedRecommendationId: null }, { baselineBlastRadius: 100 }, { ...o, descriptions: { a: long }, reasons: { a: "Not enough worker nodes for a safe spread." } });

    const details = find(c, (n) => n.tag === "details")[0];
    assert.equal(find(details, (n) => /candidate-card__description/.test(n.className))[0].textContent, long);
    assert.equal(find(details, (n) => /candidate-card__reason/.test(n.className))[0].textContent, "Not enough worker nodes for a safe spread.");
    assert.equal(find(c, (n) => /candidate-card__description|candidate-card__reason/.test(n.className) && n.tag === "p").length, 2);
});

test("the plain view hides the repeated outcome sentence, shows no evidence list unless it needs a look, and keeps the proposal's facts one click away", () => {
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /data-view="simple"\][^{]*rec-outcome\[data-state="ready"\][^{]*\{[^}]*display:\s*none/);
    assert.match(css, /data-view="simple"\][^{]*rec-evidence-details:not\(\[data-attention="true"\]\)[^{]*\{[^}]*display:\s*none/);
    assert.match(pageTemplate, /<details class="rec-more rec-proposal-details">\s*<summary>Proposal details<\/summary>\s*<div class="rec-proposal" data-role="rec-proposal-summary">/);
});

test("only one step is drawn at a time, and the declared steps are the template's own", () => {
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /\.rec-flow \[data-step-panel\]\s*\{\s*display:\s*none/);
    for (const n of [1, 2, 3, 4, 5]) assert.ok(css.includes(`.rec-flow[data-step="${n}"] [data-step-panel="${n}"]`), `step ${n}`);
    for (const n of [1, 2, 3, 4, 5]) assert.ok(templateHtml.includes(`data-step-panel="${n}"`), `a panel for step ${n}`);
});

test("the header and the steps carry one short sentence each, not paragraphs", () => {
    const header = templateHtml.slice(templateHtml.indexOf('class="page-header__subtitle"'), templateHtml.indexOf("</p>", templateHtml.indexOf('class="page-header__subtitle"')));
    assert.ok(header.replace(/\\s+/g, " ").length < 160, header);
    for (const sub of [...templateHtml.matchAll(/class="rec-panel__sub"[^>]*>([^<]*)</g)].map((m) => m[1])) assert.ok(sub.length < 60, sub);
    assert.ok(!templateHtml.includes("When there is only one choice it is used for you"), "step 1's explanation is gone");
});

// ---- the plain view's short answer --------------------------------------------------------------------------------------------

test("with many alternatives only the lead card is open; the rest sit behind one disclosure that counts them", () => {
    const o = options(); const c = o.document.container();
    const make = (id, type) => ({ id, type, service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 });
    const many = ["a", "b", "c", "d"].map((id) => make(id, "REPLICA_INCREASE"));

    view.renderCandidateCards(c, { recommendations: many, selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, { ...o, hero: { problem: "p", validationText: "v" } });

    assert.equal(c.children.length, 2, "the lead card and one disclosure");
    assert.ok(/candidate-card--lead/.test(c.children[0].className));
    assert.equal(c.children[1].tag, "details");
    assert.ok(text(c.children[1]).startsWith("Other options (3)"));
    assert.equal(find(c.children[1], (n) => /\bcandidate-card\b/.test(n.className)).length, 3);
});

test("with two alternatives or fewer, and with no lead card, every card is simply listed", () => {
    const o = options();
    const make = (id) => ({ id, type: "REPLICA_INCREASE", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 });
    const few = o.document.container();
    view.renderCandidateCards(few, { recommendations: [make("a"), make("b"), make("c")], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, { ...o, hero: { problem: "p", validationText: "v" } });
    assert.equal(few.children.length, 3);
    const none = o.document.container();
    view.renderCandidateCards(none, { recommendations: ["a", "b", "c", "d"].map(make), selectedRecommendationId: null }, { baselineBlastRadius: 100 }, o);
    assert.equal(none.children.length, 4, "with nothing recommended there is no lead to put the others behind");
});

test("a prediction of no gain is worded as no change, and is not drawn in the colour of a gain", () => {
    const o = options(); const c = o.document.container();
    const same = { id: "a", type: "REPLICA_INCREASE", service: "payment", feasible: true, predictedBlastRadius: 100, riskReduction: 0, resourceCost: "MEDIUM", confidence: 0.9, score: 0.1 };

    view.renderCandidateCards(c, { recommendations: [same], selectedRecommendationId: null }, { baselineBlastRadius: 100 }, o);

    const delta = find(c, (n) => /candidate-card__delta/.test(n.className))[0];
    assert.equal(delta.textContent, "No change");
    assert.match(delta.className, /candidate-card__delta--none/);
});

test("the problem is stated from the engine's own impact, and not at all when the engine reported none", () => {
    assert.equal(view.problemStatement(detail()), "If w1 fails, the workflow does not survive.");
    assert.equal(view.problemStatement(detail({ baselineImpact: { blastRadius: 0, failedServices: [], failedWorkflows: [] } })), "If w1 fails, the workflow survives.");
    assert.equal(view.problemStatement(detail({ baselineImpact: null })), null);
    assert.equal(view.problemStatement(null), null);
});

test("strategy and cost are written in sentence case, and a cost the engine did not report is said to be unreported", () => {
    assert.equal(view.strategyName("POD_ANTI_AFFINITY"), "Pod anti affinity");
    assert.equal(view.strategyName(null), "—");
    assert.equal(view.costLabel("MEDIUM"), "Medium cost");
    assert.equal(view.costLabel(null), "Cost not reported");
    assert.equal(view.costLabel(""), "Cost not reported");
});

test("the impact step is a verdict, the bar, the services that go with the node and nothing else; the four figures and the table are the research reading", () => {
    const o = options(); const c = o.document.container();

    view.renderImpactSummary(c, withRoles(), o);

    const research = find(c, (n) => /rec-research-only/.test(n.className));
    assert.equal(research.length, 1, "only the four figures");
    assert.ok(text(research[0]).includes("Workflow survival") && text(research[0]).includes("Failed node"));
    const bar = find(c, (n) => /\bgauge\b/.test(n.className));
    assert.equal(bar.length, 1);
    assert.ok(text(bar[0]).includes("Lost") && text(bar[0]).includes("100%"));
    assert.ok(text(c).includes("Predicted from the live topology, not measured."));
    const table = find(c, (n) => n.tag === "details");
    assert.equal(table.length, 1, "the services table is one click away");
    assert.ok(text(table[0]).startsWith("Services in this workflow"));
});
