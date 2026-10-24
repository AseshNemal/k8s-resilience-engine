// Logic tests for app/static/js/recommendation-portfolio.js (the "across all workflows" panel). Run with: node --test tests/frontend/
// The panel draws what the server returns. It decides nothing about survival, picks no workflow for approval, and an answer for an
// older request never replaces a newer one.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/recommendation-portfolio.js", import.meta.url), "utf8");
const window = {};
vm.runInNewContext(source, { window, console, URLSearchParams });
const portfolio = window.ResilienceRecommendationPortfolio;
const plain = (value) => JSON.parse(JSON.stringify(value));

function makeDocument() {
    const create = (tag) => ({
        tag, children: [], attributes: {}, className: "", textContent: "", style: {}, listeners: {}, hidden: false, disabled: false, value: "",
        setAttribute(name, value) { this.attributes[name] = String(value); },
        append(...nodes) { this.children.push(...nodes); },
        appendChild(node) { this.children.push(node); return node; },
        replaceChildren(...nodes) { this.children = [...nodes]; },
        addEventListener(type, fn) { this.listeners[type] = fn; },
    });
    return { createElement: create, create };
}
const walk = (node, visit) => { visit(node); (node.children || []).forEach((child) => walk(child, visit)); };
const find = (node, predicate) => { const out = []; walk(node, (n) => { if (predicate(n)) out.push(n); }); return out; };
const textOf = (node) => { const parts = []; walk(node, (n) => { if (n.textContent) parts.push(n.textContent); }); return parts.join(" | "); };

const workflows = [{ workflowId: "checkout", criticality: 0.9 }, { workflowId: "browse", criticality: 0.3 }];
const part = (service, protects) => ({ candidateId: `rec-all-workflows-${service}-topology-spread`, service, type: "TOPOLOGY_SPREAD", protects });
const candidate = (overrides) => ({
    id: "rec-1", type: "TOPOLOGY_SPREAD", service: "db", feasible: true, selected: false,
    ranking: { rank: 1 }, cost: { resource: "LOW" }, feasibility: { explanation: null },
    workflows: { protected: ["checkout"], exposed: ["browse"], exposedAt: { browse: ["w2"] } },
    coverage: { worstCaseBefore: 75, worstCaseAfter: 25, protectsEveryWorkflow: false },
    parts: [part("db", ["checkout"])], ...overrides,
});
const pair = candidate({
    id: "rec-pair", type: "COMBINATION", service: "cache+db", selected: true,
    workflows: { protected: ["checkout", "browse"], exposed: [], exposedAt: {} },
    coverage: { worstCaseBefore: 75, worstCaseAfter: 0, protectsEveryWorkflow: true },
    parts: [part("cache", ["browse"]), part("db", ["checkout"])],
});
const detail = (...candidates) => ({ portfolio: { workflows, notEvaluated: [], note: "Approval is for one workflow." }, candidates });

// ---- which source, and the request ------------------------------------------------------------------------------

const wf = (id, extra) => ({ workflowId: id, applicable: true, declared: false, ...extra });

test("it needs two usable workflows of one kind, and never mixes frozen with declared", () => {
    assert.equal(portfolio.pickSource([wf("a"), wf("b")]), "frozen");
    assert.equal(portfolio.pickSource([wf("a", { declared: true }), wf("b", { declared: true })]), "declared");
    assert.equal(portfolio.pickSource([wf("a")]), null, "one workflow: the ordinary review is the same answer");
    assert.equal(portfolio.pickSource([wf("a"), wf("b", { declared: true }), wf("c", { declared: true })]), null, "one frozen and several declared are not mixed");
    assert.equal(portfolio.pickSource([wf("a"), wf("b", { applicable: false })]), null, "a workflow that does not apply here does not count");
    assert.equal(portfolio.pickSource([]), null);
});

test("frozen workflows win over declared ones when both can be used", () => {
    assert.equal(portfolio.pickSource([wf("a"), wf("b"), wf("c", { declared: true }), wf("d", { declared: true })]), "frozen");
});

test("the request names the source and the application, and the failure only when one was chosen", () => {
    assert.equal(portfolio.portfolioUrl("declared", "bookinfo", ""), "/api/recommendations/portfolio/detail?source=declared&namespace=bookinfo");
    assert.equal(portfolio.portfolioUrl("frozen", "shop", "w2"), "/api/recommendations/portfolio/detail?source=frozen&namespace=shop&failed_node=w2");
});

// ---- the sentence and the review options -------------------------------------------------------------------------

test("the headline says all workflows only when the server says nothing stays exposed", () => {
    assert.match(portfolio.headline(detail(pair)).text, /protects all 2 workflows/);
    const some = portfolio.headline(detail(candidate({})));
    assert.equal(some.kind, "some");
    assert.match(some.text, /protects 1 of 2 workflows/);
    assert.match(some.text, /browse would still break/);
});

test("there is no headline when no change helps", () => {
    assert.equal(portfolio.headline(detail(candidate({ feasible: false, ranking: { rank: null } }))), null);
    assert.equal(portfolio.headline(detail()), null);
});

test("a part is offered for the workflows it protects, most critical first", () => {
    assert.deepEqual(plain(portfolio.reviewOptions(part("x", ["browse", "checkout"]), detail())), ["checkout", "browse"]);
});

test("a part of a combined fix is also offered what the whole fix protects, since a workflow that needs both is protected by neither alone", () => {
    const whole = { workflows: { protected: ["checkout"] } };
    assert.deepEqual(plain(portfolio.reviewOptions(part("x", ["browse"]), detail(), whole)), ["checkout", "browse"]);
});

test("a part that protects nothing alone is offered for every evaluated workflow, most critical first", () => {
    assert.deepEqual(plain(portfolio.reviewOptions(part("x", []), detail())), ["checkout", "browse"]);
});

// ---- the panel ----------------------------------------------------------------------------------------------------

function makePanel(answer, reviews) {
    const document = makeDocument();
    const root = document.create("section");
    const requests = [];
    const panel = portfolio.createPortfolioPanel({
        root, document, strategyName: (code) => String(code).toLowerCase().replace(/_/g, " "),
        fetchJson: (url) => { requests.push(url); return answer(url); }, onReview: (choice) => reviews.push(plain(choice)),
    });
    return { panel, root, requests };
}

test("it draws the ranked changes with what each protects and leaves exposed, and the lead sentence", async () => {
    const { panel, root, requests } = makePanel(async () => detail(pair, candidate({ id: "rec-2", ranking: { rank: 2 } })), []);

    await panel.load("declared", "bookinfo", "");

    assert.deepEqual(requests, ["/api/recommendations/portfolio/detail?source=declared&namespace=bookinfo"]);
    const text = textOf(root);
    assert.match(text, /Across all workflows/);
    assert.match(text, /Declared workflows · research only/);
    assert.match(text, /2 workflows weighed by criticality: checkout \(0\.9\), browse \(0\.3\)/);
    assert.match(text, /protects all 2 workflows/);
    assert.match(text, /Protects every workflow/);
    assert.match(text, /browse if w2 fails/);
    assert.match(text, /75% → 0%/);
    assert.equal(find(root, (n) => n.attributes["data-lead"] === "true").length, 1);
});

test("a combined fix is titled by its parts and reviewed part by part, each for a workflow it protects", async () => {
    const reviews = [];
    const { panel, root } = makePanel(async () => detail(pair), reviews);
    await panel.load("declared", "bookinfo", "");

    const use = find(root, (n) => n.tag === "button" && n.textContent === "Use this fix")[0];
    use.listeners.click();

    const text = textOf(root);
    assert.match(text, /topology spread on cache \+ topology spread on db/);
    assert.match(text, /review each part for a workflow it protects/);
    const reviewButtons = find(root, (n) => n.tag === "button" && n.textContent === "Review");
    assert.equal(reviewButtons.length, 2);
    const selects = find(root, (n) => n.tag === "select");
    // each part is offered what it protects alone and what the whole fix protects, most critical first
    assert.deepEqual(selects.map((select) => find(select, (n) => n.tag === "option").map((o) => o.value)), [["checkout", "browse"], ["checkout", "browse"]]);

    selects[0].value = "browse";
    reviewButtons[0].listeners.click();
    assert.deepEqual(reviews, [{ workflowId: "browse", source: "declared", service: "cache", type: "TOPOLOGY_SPREAD" }]);
});

test("choosing a fix opens its review options and nothing is sent until Review is pressed", async () => {
    const reviews = [];
    const { panel, root } = makePanel(async () => detail(candidate({})), reviews);
    await panel.load("frozen", "shop", "");

    find(root, (n) => n.tag === "button" && n.textContent === "Use this fix")[0].listeners.click();

    assert.deepEqual(reviews, []);
    assert.match(textOf(root), /Approval is for one change under one workflow/);
});

test("a workflow the server could not evaluate is named with its reason, not dropped silently", async () => {
    const answer = detail(candidate({}));
    answer.portfolio.notEvaluated = [{ workflowId: "reports", code: "TOPOLOGY_INCOMPLETE", message: "services not placed" }];
    const { panel, root } = makePanel(async () => answer, []);

    await panel.load("declared", "bookinfo", "");

    assert.match(textOf(root), /Not evaluated: reports\. services not placed/);
});

test("when no change helps it says so instead of showing an empty list", async () => {
    const { panel, root } = makePanel(async () => detail(candidate({ feasible: false, ranking: { rank: null } })), []);

    await panel.load("frozen", "shop", "");

    assert.match(textOf(root), /No change was found that reduces the risk/);
});

test("a failed request is shown with the server's code and the panel stays usable", async () => {
    const { panel, root } = makePanel(async () => { throw Object.assign(new Error("No workflow is declared."), { code: "DECLARED_WORKFLOW_UNAVAILABLE" }); }, []);

    await panel.load("declared", "bookinfo", "");

    assert.match(textOf(root), /No workflow is declared\. \(DECLARED_WORKFLOW_UNAVAILABLE\)/);
    assert.equal(find(root, (n) => n.attributes.role === "alert").length, 1);
});

test("the same request is not sent twice, and an answer for an older request is dropped", async () => {
    const waiting = [];
    const { panel, root, requests } = makePanel(() => new Promise((resolve) => waiting.push(resolve)), []);

    const first = panel.load("declared", "bookinfo", "");
    panel.load("declared", "bookinfo", "");                       // the same question while it is already being asked
    assert.equal(requests.length, 1);
    const second = panel.load("declared", "bookinfo", "w2");      // a newer question
    waiting[1](detail(pair));
    await second;
    waiting[0](detail(candidate({ id: "stale" })));                // the older answer arrives late
    await first;

    assert.equal(find(root, (n) => n.attributes["data-candidate"] === "stale").length, 0);
    assert.equal(find(root, (n) => n.attributes["data-candidate"] === "rec-pair").length, 1);
});

test("clearing hides the panel and drops an answer still on its way", async () => {
    const waiting = [];
    const { panel, root } = makePanel(() => new Promise((resolve) => waiting.push(resolve)), []);

    const pending = panel.load("frozen", "shop", "");
    panel.clear();
    waiting[0](detail(pair));
    await pending;

    assert.equal(root.hidden, true);
    assert.equal(root.children.length, 0);
});

test("all text goes through textContent, so a hostile workflow name is shown as text", async () => {
    const answer = detail(candidate({ parts: [part("db<img src=x onerror=alert(1)>", ["checkout"])] }));
    const { panel, root } = makePanel(async () => answer, []);

    await panel.load("frozen", "shop", "");

    assert.equal(find(root, (n) => n.tag === "img").length, 0);
});
