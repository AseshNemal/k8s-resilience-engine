// Frontend test for the Member 4 Development Lab page script: the real lab.js runs against a stubbed
// network and a fake DOM. Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const SOURCE = readFileSync(new URL("../../experiments/member4_dev_lab/static/lab.js", import.meta.url), "utf8");

function makeEnvironment(routes) {
    const registry = new Map();
    const element = (key) => {
        if (registry.has(key)) return registry.get(key);
        const node = makeNode(key);
        registry.set(key, node);
        return node;
    };
    const makeNode = (key) => ({
        key, children: [], className: "", textContent: "", style: {}, listeners: {}, value: "", hidden: false,
        classList: { _set: new Set(), add(c) { this._set.add(c); }, remove(c) { this._set.delete(c); }, toggle(c, force) { const on = force === undefined ? !this._set.has(c) : force; on ? this._set.add(c) : this._set.delete(c); return on; }, contains(c) { return this._set.has(c); } },
        append(...nodes) { this.children.push(...nodes); },
        appendChild(node) { this.children.push(node); return node; },
        removeChild(node) { this.children = this.children.filter((child) => child !== node); return node; },
        get firstChild() { return this.children[0] || null; },
        setAttribute(name, value) { (this.attributes ||= {})[name] = value; },
        addEventListener(type, fn) { this.listeners[type] = fn; },
    });
    const listeners = {};
    const document = {
        querySelector: (selector) => element(selector),
        createElement: (tag) => makeNode(tag),
        createTextNode: (text) => ({ textContent: text }),
        addEventListener: (type, fn) => { listeners[type] = fn; },
    };
    const calls = [];
    const fetch = async (url) => {
        calls.push(url);
        const route = routes[url];
        if (!route) throw new Error(`unexpected request ${url}`);
        return { ok: route.status < 400, status: route.status, json: async () => route.body };
    };
    const window = {};
    const sandbox = { window, document, fetch, console, URLSearchParams, setTimeout, clearTimeout };
    vm.createContext(sandbox);
    vm.runInContext(SOURCE, sandbox, { filename: "lab.js" });
    return { element, listeners, calls };
}

function text(node) {
    const parts = [];
    const visit = (n) => { if (n && n.textContent) parts.push(n.textContent); (n.children || []).forEach(visit); };
    visit(node);
    return parts.join(" ");
}

const SCENARIOS = {
    label: "OFFLINE",
    scenarios: [
        { name: "concentrated-replicas", objective: "obj", situation: "concentrated replicas", judgedService: "reviews", judgedStrategy: "TOPOLOGY_SPREAD", expected: { feasibility: "FEASIBLE", yaml: "GENERATED", selected: true, documents: 1 } },
        { name: "taints-unread", objective: "obj", situation: "taints unread", judgedService: "reviews", judgedStrategy: "TOPOLOGY_SPREAD", expected: { feasibility: "INSUFFICIENT_EVIDENCE", yaml: "INSUFFICIENT_EVIDENCE", selected: false, documents: 1 } },
    ],
};

const DETAIL = {
    label: "OFFLINE",
    provenance: { source: "offline-test-fixture", trustedLiveSource: "live-discovery", isLiveEvidence: false, note: "note" },
    inputs: {
        name: "concentrated-replicas", objective: "Show the pipeline", situation: "concentrated", failedNode: "w1",
        workflow: { workflowId: "productpage-view", criticality: 0.9, mandatoryServices: ["productpage", "reviews"], optionalServices: [], edges: [{ source: "productpage", target: "reviews" }] },
        extraWorkflows: [],
        deployment: {
            snapshotId: "s1", failureDomainType: "kubernetes.io/hostname",
            nodes: [
                { name: "w1", zone: "zone-a", ready: true, schedulable: true, cpuAvailable: 4, memoryAvailable: 8, labels: {} },
                { name: "w2", zone: "zone-b", ready: true, schedulable: true, cpuAvailable: 4, memoryAvailable: 8, labels: {} },
            ],
            services: [{ name: "reviews", namespace: "n", declaredReplicas: 3, readyReplicas: 3, replicaConcentration: 1, concentrated: true, hostingNodes: ["w1"], intent: null,
                pods: [{ name: "reviews-0", node: "w1", ready: true }, { name: "reviews-1", node: "w1", ready: true }, { name: "reviews-2", node: "w1", ready: true }] }],
        },
        schedulingEvidence: { nodeTaints: { w1: [], w2: [] }, note: "taints note" },
    },
    currentRisk: { workflowId: "productpage-view", failedNode: "w1", blastRadius: 100, survivability: 0, failedServices: ["reviews"], failedWorkflows: ["productpage-view"],
        rootCauses: [{ type: "REPLICA_CONCENTRATION", service: "reviews", failureDomain: "w1", concentration: 1 }],
        nodeRiskRanking: [{ node: "w1", blastRadius: 100 }, { node: "w2", blastRadius: 0 }] },
    detail: {
        outcome: { state: "READY", message: "1 candidate(s) can be put to a developer for approval." },
        report: { workflowId: "productpage-view", selectedRecommendationId: "rec-1", recommendations: [] },
        evidence: { workflowId: "productpage-view", isPrediction: true },
        candidates: [{
            id: "rec-1", type: "TOPOLOGY_SPREAD", service: "reviews", rootCause: "all replicas on w1",
            recommendedChange: "add a topologySpreadConstraint", feasible: true,
            feasibility: { status: "FEASIBLE", explanation: "a placement was found", findings: [{ constraint: "capacity", status: "SATISFIED", detail: "fits", node: "w2" }], missingEvidence: [] },
            currentPlacement: { reviews: [{ pod: "reviews-0", node: "w1", ready: true }] },
            declaredReplicas: 3, readyReplicas: 3, podsToRecreate: ["reviews-1"],
            predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0, riskReduction: 100 },
            cost: { resource: "LOW", note: "no replicas added", configuration: "1 Deployment pod-template edit(s)" },
            confidence: 1, score: 0.91, ranking: null, selected: true, selectionJustification: "Selected: top-ranked feasible candidate.",
            approval: { ready: true, reason: null, requiresJustification: false },
            simulatedDeployment: { nodes: [{ name: "w1", zone: "zone-a" }, { name: "w2", zone: "zone-b" }], services: [{ name: "reviews", pods: [{ name: "reviews-0", node: "w1", ready: true }, { name: "reviews-1", node: "w2", ready: true }] }] },
            yamlProposal: { status: "GENERATED", applicable: false }, notes: [],
        }],
        currentDeployment: { nodes: [{ name: "w1" }, { name: "w2" }], services: [] },
        yaml: { available: true, recommendationId: "rec-1", text: "# proposal\nspec: {}", sha256: "abc123def456", applicable: false, reason: "", disclosures: ["disclosure"], podReplacement: { included: false, note: "no pods" } },
        validation: { available: false }, applied: false,
    },
    extras: { "rec-1": { addedReplicas: [] } },
    feasibility: { "rec-1": { status: "FEASIBLE", assumptions: ["scheduler honours the constraint"], notModeled: ["host ports"] } },
    ranking: { formulaVersion: "v1", basis: "single-workflow", placementChurnMeaning: "churn", selectedCandidate: "rec-1",
        weights: { targetShare: 0.7, disruptionDiscount: 0.3, resourceDiscount: 0.3, status: "experimental" },
        candidates: { "rec-1": { id: "rec-1", type: "TOPOLOGY_SPREAD", service: "reviews", rank: 1, score: 0.91, excluded: null, detail: "best", components: { benefit: 1, churnFactor: 0.9, resourceFactor: 1, confidence: 1 } } } },
    combinations: {},
    validation: {
        available: true, label: "controlled offline validation", recommendationId: "rec-1",
        target: { name: "offline-double", environment: "test" }, approvalGate: "gate", baseline: "DERIVED_FROM_PLACEMENT",
        runs: {
            schedulerSpreads: { recommendationId: "rec-1", state: "COMPLETED", validated: true, reasons: [], predictionError: 0, isMeasurement: true,
                comparison: {
                    blastRadius: { predictedBefore: 100, observedBefore: 100, predictedAfter: 0, observedAfter: 0 },
                    riskReduction: { predicted: 100, observed: 100, observedBasis: "DERIVED_FROM_PLACEMENT" },
                    serviceSurvival: { agreement: "2/2", services: [{ service: "reviews", predictedSurvives: true, observedSurvives: true, match: true }] },
                    mandatoryWorkflowSurvival: { workflowId: "productpage-view", predictedSurvives: true, observedSurvives: true, match: true, mandatoryServices: ["reviews"] },
                } },
            schedulerChangesNothing: { recommendationId: "rec-1", state: "COMPLETED", validated: false, reasons: ["blast radius differs"], predictionError: 100, isMeasurement: true, comparison: {} },
        },
    },
    customWeights: null,
};

const ROUTES = {
    "/api/dev-lab/scenarios": { status: 200, body: SCENARIOS },
    "/api/dev-lab/scenarios/concentrated-replicas": { status: 200, body: DETAIL },
};

async function openLab(routes = ROUTES) {
    const env = makeEnvironment(routes);
    await env.listeners.DOMContentLoaded();
    await new Promise((resolve) => setTimeout(resolve, 0));   // let start()'s fetch + render finish
    return env;
}

test("the scenario list is rendered from the API", async () => {
    const env = await openLab();
    const list = text(env.element('[data-role="scenario-list"]'));
    assert.match(list, /concentrated-replicas/);
    assert.match(list, /taints-unread/);
    assert.match(list, /concentrated replicas/);
    assert.deepEqual(env.calls, ["/api/dev-lab/scenarios"]);
});

test("selecting a scenario renders every pipeline section", async () => {
    const env = await openLab();
    const listNode = env.element('[data-role="scenario-list"]');
    const firstButton = listNode.children[0].children[0];
    await firstButton.listeners.click();

    assert.ok(env.calls.includes("/api/dev-lab/scenarios/concentrated-replicas"));
    assert.match(text(env.element('[data-role="inputs-body"]')), /productpage-view/);
    assert.match(text(env.element('[data-role="risk-body"]')), /REPLICA_CONCENTRATION/);
    assert.match(text(env.element('[data-role="risk-diagram"]')), /reviews-0/);
    assert.match(text(env.element('[data-role="candidates-body"]')), /TOPOLOGY_SPREAD/);
    assert.match(text(env.element('[data-role="candidates-body"]')), /FEASIBLE/);
    const simulation = text(env.element('[data-role="simulation-body"]'));
    assert.match(simulation, /offline simulation/);
    assert.match(simulation, /scheduler honours the constraint/);
    assert.match(simulation, /host ports/);
    assert.match(text(env.element('[data-role="ranking-body"]')), /top-ranked feasible candidate/);
    assert.equal(env.element('[data-role="yaml-text"]').textContent, "# proposal\nspec: {}");
    assert.match(text(env.element('[data-role="yaml-status"]')), /applicable: false/);
    const validation = text(env.element('[data-role="validation-body"]'));
    assert.match(validation, /VALIDATED/);
    assert.match(validation, /NOT VALIDATED/);
    assert.equal(env.element('[data-role="weight-target-share"]').value, 0.7);
});

test("a failed detail request is shown as an error, not swallowed", async () => {
    const env = await openLab({
        "/api/dev-lab/scenarios": { status: 200, body: SCENARIOS },
        "/api/dev-lab/scenarios/concentrated-replicas": { status: 404, body: { detail: "Unknown scenario" } },
    });
    const firstButton = env.element('[data-role="scenario-list"]').children[0].children[0];
    await firstButton.listeners.click();
    assert.match(text(env.element('[data-role="placeholder"]')), /Unknown scenario/);
});

test("re-evaluating with custom weights requests the weights as query parameters", async () => {
    const env = await openLab();
    const firstButton = env.element('[data-role="scenario-list"]').children[0].children[0];
    await firstButton.listeners.click();
    env.element('[data-role="weight-target-share"]').value = "0.1";
    env.element('[data-role="weight-disruption-discount"]').value = "0.3";
    env.element('[data-role="weight-resource-discount"]').value = "0.3";
    // the stub answers any weighted URL with the same detail body
    const url = "/api/dev-lab/scenarios/concentrated-replicas?target_share=0.1&disruption_discount=0.3&resource_discount=0.3";
    const originalFetchError = env.calls.length;
    try {
        await env.element('[data-role="apply-weights"]').listeners.click();
    } catch { /* the stub route is missing on purpose if the URL is wrong */ }
    assert.equal(env.calls[originalFetchError], url);
});
