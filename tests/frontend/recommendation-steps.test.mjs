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

test("nothing chosen: the first step is current and the rest wait", () => {
    assert.deepEqual(states({ chosen: false, status: "loading", selected: false, applied: false }), ["current", "todo", "todo", "todo", "todo"]);
});

test("while the recommendation is being asked for, the second step is current", () => {
    assert.deepEqual(states({ chosen: true, status: "loading-recommendation" }), ["done", "current", "todo", "todo", "todo"]);
});

test("missing evidence blocks the second step and nothing after it is marked done", () => {
    assert.deepEqual(states({ chosen: true, status: "missing-evidence", selected: true, applied: true }), ["done", "blocked", "todo", "todo", "todo"]);
});

test("a recommendation with no selected candidate blocks the third step", () => {
    assert.deepEqual(states({ chosen: true, status: "ready", selected: false }), ["done", "done", "blocked", "todo", "todo"]);
});

test("a selected candidate leaves the last step as the one to do, and an applied change completes it", () => {
    assert.deepEqual(states({ chosen: true, status: "ready", selected: true, applied: false }), ["done", "done", "done", "todo", "current"]);
    assert.deepEqual(states({ chosen: true, status: "ready", selected: true, applied: true }), ["done", "done", "done", "todo", "done"]);
});

test("proving is optional: it is Not yet while nothing has run, the approval is still Now, and a passed proof completes it", () => {
    const base = { chosen: true, status: "ready", selected: true, applied: false };
    assert.deepEqual(states({ ...base, proof: "none" }), ["done", "done", "done", "todo", "current"]);
    assert.deepEqual(states({ ...base, proof: "passed" }), ["done", "done", "done", "done", "current"]);
});

test("while a proof is running it is the current step and the approval waits; a failed or attention-needing proof is flagged", () => {
    const base = { chosen: true, status: "ready", selected: true, applied: false };
    assert.deepEqual(states({ ...base, proof: "running" }), ["done", "done", "done", "current", "todo"]);
    assert.deepEqual(states({ ...base, proof: "failed" }), ["done", "done", "done", "blocked", "current"]);
    assert.deepEqual(states({ ...base, proof: "attention" }), ["done", "done", "done", "blocked", "current"]);
});

test("a proof is never shown before there is a candidate to prove", () => {
    assert.deepEqual(states({ chosen: true, status: "ready", selected: false, proof: "passed" }), ["done", "done", "blocked", "todo", "todo"]);
});

test("the stepper names the state in words as well as marks, and marks only the current step for assistive technology", () => {
    const o = options(); const c = o.document.container();

    view.renderStepper(c, view.computeSteps({ chosen: true, status: "ready", selected: false }), o);

    const items = find(c, (n) => n.tag === "li");
    assert.equal(items.length, 5);
    assert.deepEqual(items.map((n) => n.attributes["data-state"]), ["done", "done", "blocked", "todo", "todo"]);
    assert.equal(items.filter((n) => n.attributes["aria-current"] === "step").length, 0, "no step is current while one needs attention");
    assert.ok(text(items[2]).includes("Needs attention") && text(items[0]).includes("Done") && text(items[3]).includes("Not yet"));
    assert.ok(text(items[0]).includes("✓") && text(items[2]).includes("!") && text(items[3]).includes("4") && text(items[4]).includes("5"));

    view.renderStepper(c, view.computeSteps({ chosen: false }), o);
    assert.equal(find(c, (n) => n.attributes["aria-current"] === "step").length, 1);
});

test("the evidence summary counts what is available and asks for attention when something is missing", () => {
    assert.deepEqual(plain(view.evidenceSummary([{ state: "available" }, { state: "pending" }])), { text: "Evidence: 1 of 2 available", attention: false });
    assert.deepEqual(plain(view.evidenceSummary([{ state: "available" }, { state: "missing" }])), { text: "Evidence: 1 of 2 available", attention: true });
    assert.deepEqual(plain(view.evidenceSummary([{ state: "incomplete" }])), { text: "Evidence: 0 of 1 available", attention: true });
    assert.deepEqual(plain(view.evidenceSummary([])), { text: "Evidence", attention: false });
});

// ---- candidate cards in the plain view ---------------------------------------------------------------------------------

test("a candidate card says what it changes and why it cannot be put forward; confidence and score are research-only", () => {
    const o = options(); const c = o.document.container();
    const feasible = { id: "a", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 };
    const blocked = { ...feasible, id: "b", type: "REPLICA_INCREASE", feasible: false };

    view.renderCandidateCards(c, { recommendations: [feasible, blocked], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, {
        ...o, descriptions: { a: "Add a spread constraint." }, reasons: { b: "Not enough worker nodes." },
    });

    assert.ok(text(c).includes("Add a spread constraint.") && text(c).includes("Not enough worker nodes."));
    const research = find(c, (n) => /rec-research-only/.test(n.className));
    assert.equal(research.length, 4, "confidence and score on each of the two cards");
    assert.ok(research.every((n) => /Confidence|Score/.test(text(n))));
    const costStats = find(c, (n) => n.children.length === 2 && text(n).startsWith("Resource Cost"));
    assert.equal(costStats.length, 2, "the cost is on each card");
    assert.ok(costStats.every((n) => !/rec-research-only/.test(n.className)), "the cost stays in the plain view");
});

// ---- the template: every block belongs to a view ------------------------------------------------------------------------

const pageTemplate = templateHtml.slice(templateHtml.indexOf('<template data-role="page-template">'), templateHtml.indexOf("</template>", templateHtml.indexOf('<template data-role="page-template">')));
const researchOnly = [...templateHtml.matchAll(/<section[^>]*rec-research-only[^>]*>[\s\S]*?<\/section>/g)].map((m) => m[0]);
const inResearchOnly = (role) => researchOnly.some((block) => block.includes(`data-role="${role}"`));

test("every block drawn for a recommendation is a step or is marked research-only, so none is shown by accident in either view", () => {
    const sections = [...pageTemplate.matchAll(/<section class="([^"]*)"/g)].map((m) => m[1]);
    assert.ok(sections.length >= 4);
    for (const classes of sections) assert.ok(/\brec-step\b|\brec-research-only\b/.test(classes), classes);
});

test("the plain view keeps the steps' own parts and the research view alone keeps the ranking, the reasons and the raw data", () => {
    for (const role of ["rec-impact", "rec-candidate-list", "rec-outcome", "rec-proposal-summary", "approval-state-container", "approval-actions", "rec-placement-current"]) {
        assert.ok(pageTemplate.includes(`data-role="${role}"`), role);
        assert.ok(!inResearchOnly(role), `${role} must be visible in the plain view`);
    }
    for (const role of ["rec-detail-list", "rec-candidate-table-body", "rec-validation", "rec-yaml", "rec-failures"]) {
        assert.ok(inResearchOnly(role), `${role} belongs to the research view`);
    }
});

test("the prediction-not-observation statement stays in the plain view", () => {
    assert.ok(pageTemplate.includes("Predictions from a simulation, not measurements."));
    assert.ok(!researchOnly.some((block) => block.includes("Predictions from a simulation, not measurements.")));
    assert.ok(pageTemplate.includes('data-role="rec-validation-note"') && !inResearchOnly("rec-validation-note"));
});

test("approval and apply stay two separate actions: no single button approves and applies", () => {
    assert.ok(!/Approve and Implement|Approve and Apply/i.test(templateHtml));
    assert.ok(pageTemplate.includes("Approving changes nothing on the cluster."));
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
    assert.ok(shown.includes("Topology Spread for payment") && shown.includes("topologySpreadConstraint"));
    assert.ok(shown.includes("100%") && shown.includes("0%") && shown.includes("Low"));
    assert.ok(shown.includes("Predicted, not observed."));
    assert.equal(find(c, (n) => n.attributes["data-kind"] === "topology_spread").length, 1);
});

test("with nothing selected the approval step says so instead of showing an improvement", () => {
    const o = options(); const c = o.document.container();

    view.renderSelectedImprovement(c, null, o);

    assert.ok(text(c).includes("Nothing to approve"));
    assert.equal(find(c, (n) => /rec-selected__outcome/.test(n.className)).length, 0);
});

test("only the selected candidate carries the check, and every card carries an icon for its strategy", () => {
    const o = options(); const c = o.document.container();
    const one = { id: "a", type: "TOPOLOGY_SPREAD", service: "payment", feasible: true, predictedBlastRadius: 0, riskReduction: 100, resourceCost: "LOW", confidence: 0.9, score: 0.8 };
    const two = { ...one, id: "b", type: "POD_ANTI_AFFINITY", feasible: false };

    view.renderCandidateCards(c, { recommendations: [one, two], selectedRecommendationId: "a" }, { baselineBlastRadius: 100 }, o);

    assert.equal(find(c, (n) => /candidate-card__check/.test(n.className)).length, 1);
    assert.deepEqual(find(c, (n) => /candidate-card__icon/.test(n.className)).map((n) => n.attributes["data-kind"]).sort(), ["pod_anti_affinity", "topology_spread"]);
});

test("step 1 holds the one action, and the step 4 parts and the proposal's facts are all in the plain view", () => {
    const step1 = templateHtml.slice(templateHtml.indexOf('data-role="rec-context"'), templateHtml.indexOf('data-role="rec-operation"'));
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
    const step1 = templateHtml.slice(templateHtml.indexOf('data-role="rec-context"'), templateHtml.indexOf('data-role="rec-operation"'));
    assert.ok(step1.includes('data-role="rec-hint"') && step1.includes('id="rec-next-hint"') && step1.includes('role="status"'));
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /\.rec-inputs__input\[data-attention="true"\]\s*\{[^}]*border-color[^}]*box-shadow/, "a border and a ring");
});

// ---- less text -----------------------------------------------------------------------------------------------------------------

test("a card's long description and reason keep their full text as a tooltip, since the plain view clamps them", () => {
    const o = options(); const c = o.document.container();
    const long = "Add one replica to the Deployment together with a topologySpreadConstraint on kubernetes.io/hostname, so the added replica runs elsewhere.";
    const one = { id: "a", type: "REPLICA_INCREASE", service: "payment", feasible: false, predictedBlastRadius: 100, riskReduction: 0, resourceCost: "MEDIUM", confidence: 0.9, score: 0 };

    view.renderCandidateCards(c, { recommendations: [one], selectedRecommendationId: null }, { baselineBlastRadius: 100 }, { ...o, descriptions: { a: long }, reasons: { a: "Not enough worker nodes for a safe spread." } });

    assert.equal(find(c, (n) => /candidate-card__description/.test(n.className))[0].attributes.title, long);
    assert.equal(find(c, (n) => /candidate-card__reason/.test(n.className))[0].attributes.title, "Not enough worker nodes for a safe spread.");
});

test("the plain view clamps long card text, hides the repeated outcome sentence and keeps the proposal's facts one click away", () => {
    const css = readFileSync(new URL("../../app/static/css/pages/recommendations.css", import.meta.url), "utf8");
    assert.match(css, /\.candidate-card__description[^{]*\{[^}]*-webkit-line-clamp:\s*2/);
    assert.match(css, /data-view="research"\][^{]*candidate-card__description[^{]*\{[^}]*overflow:\s*visible/, "the research view shows it all");
    assert.match(css, /data-view="simple"\][^{]*rec-outcome\[data-state="ready"\][^{]*\{[^}]*display:\s*none/);
    assert.match(pageTemplate, /<details class="rec-proposal-details">\s*<summary>Proposal details<\/summary>\s*<div class="rec-proposal" data-role="rec-proposal-summary">/);
});

test("the header and the steps carry one short sentence each, not paragraphs", () => {
    const header = templateHtml.slice(templateHtml.indexOf('class="page-header__subtitle"'), templateHtml.indexOf("</p>", templateHtml.indexOf('class="page-header__subtitle"')));
    assert.ok(header.replace(/\s+/g, " ").length < 160, header);
    for (const sub of [...templateHtml.matchAll(/class="rec-step__sub">([^<]*)</g)].map((m) => m[1])) assert.ok(sub.length < 60, sub);
    assert.ok(!templateHtml.includes("When there is only one choice it is used for you"), "step 1's explanation is gone");
});
