// Runs the real Overview script (app/static/js/components/dashboard.js) against a minimal fake DOM, because
// a page script that throws at render time is invisible to every other test. The fake follows the real DOM
// where it matters: append() returns undefined, textContent is never parsed as markup.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(`../../app/static/js/${path}`, import.meta.url), "utf8");
const connectionState = read("connection-state.js");
const dashboard = read("components/dashboard.js");

class FakeNode {
    constructor(tag) {
        this.tagName = tag;
        this.children = [];
        this.className = "";
        this.textContent = "";
        this.hidden = false;
        this.disabled = false;
        this.open = false;
        this.listeners = {};
    }
    append(...nodes) { this.children.push(...nodes); }
    appendChild(node) { this.children.push(node); return node; }
    replaceChildren(...nodes) { this.children = nodes; }
    setAttribute() {}
    removeAttribute() {}
    addEventListener(type, handler) { this.listeners[type] = handler; }
    text() { return [this.textContent, ...this.children.map((child) => child.text())].join(" "); }
    find(predicate) {
        if (predicate(this)) return this;
        for (const child of this.children) {
            const hit = child.find(predicate);
            if (hit) return hit;
        }
        return null;
    }
}

const SELECTORS = [
    '[data-role="overview-error-region"]', '[data-role="reload-button"]', '[data-role="source-badge"]',
    '[data-role="discovery-summary"]', '[data-role="overview-namespaces"]', '[data-role="last-refreshed"]',
    '[data-role="discovery-headline"]', '[data-role="discovery-notes"]', '[data-role="discovery-provenance"]',
];

function boot(respond) {
    const nodes = Object.fromEntries(SELECTORS.map((selector) => [selector, new FakeNode("div")]));
    const calls = [];
    const diagrams = [];
    let onLoad;
    const document = {
        createElement: (tag) => new FakeNode(tag),
        querySelector: (selector) => nodes[selector] || null,
        addEventListener: (type, handler) => { if (type === "DOMContentLoaded") onLoad = handler; },
    };
    const window = {
        document,
        ResilienceTopologyCompare: { render: (target, current, after, options) => {
            diagrams.push({ current, after, options });
            const label = new FakeNode("p"); label.textContent = "Observed topology"; target.append(label);
        } },
        ResilienceApi: { fetchJson: async (url) => { calls.push(url); return respond(url); } },
        ResilienceUi: { updateLastRefreshed: (node) => { node.textContent = "refreshed"; } },
    };
    const context = vm.createContext({ window, document, console });
    vm.runInContext(connectionState, context);
    vm.runInContext(dashboard, context);
    return { nodes, calls, diagrams, load: () => onLoad() };
}

const headline = (nodes) => nodes['[data-role="discovery-headline"]'].textContent;
const region = (nodes) => nodes['[data-role="overview-namespaces"]'];

function overview(extra = {}) {
    const service = (name, ready, declared, nodes) => ({
        name, declaredReplicas: declared, readyReplicas: ready,
        pods: nodes.map((node, i) => ({ name: `${name}-${i}`, node, ready: true })),
    });
    return {
        status: "ok",
        cluster: { connectionFingerprint: "abcdef1234567890", identityVerified: false },
        capturedAt: "2026-10-19T10:00:00Z",
        excludedNamespaces: ["kube-system"],
        totals: { workerNodes: 3, services: 3, unresolved: 0 },
        namespaces: [
            { namespace: "bookinfo", status: "ok", unresolved: [], graph: { nodes: [], services: [
                service("productpage", 1, 1, ["worker2"]), service("reviews", 1, 1, ["worker3"]) ] } },
            { namespace: "resilience-test", status: "ok", unresolved: [], graph: { nodes: [], services: [
                service("paymentservice", 3, 3, ["worker", "worker", "worker"]) ] } },
        ],
        ...extra,
    };
}

test("it reads the all-namespaces endpoint, not a single namespace", async () => {
    const page = boot(() => overview());
    await page.load();

    assert.deepEqual([...page.calls], ["/api/deployments/discovery/all"]);
});

test("it shows the totals and one card per namespace with each service, its replicas and nodes", async () => {
    const page = boot(() => overview());
    await page.load();

    assert.match(headline(page.nodes), /3 services in 2 namespaces on 3 worker nodes/);
    const cards = region(page.nodes).children;
    assert.equal(cards.length, 2);
    assert.equal(cards[0].tagName, "article", "application content is always visible");
    assert.equal(page.diagrams.length, 0, "inventory does not repeat the placement graph");
    assert.match(cards[0].text(), /bookinfo/);
    assert.match(cards[0].text(), /productpage[\s\S]*1\/1 ready[\s\S]*worker2/);
    assert.match(cards[1].text(), /paymentservice[\s\S]*3\/3 ready[\s\S]*worker\b/);
    assert.equal(page.nodes['[data-role="discovery-summary"]'].hidden, false);
});

test("an unresolved pod is listed under its namespace and counted", async () => {
    const data = overview({ status: "partial", totals: { workerNodes: 3, services: 3, unresolved: 1 } });
    data.namespaces[0].status = "partial";
    data.namespaces[0].unresolved = [{ kind: "Pod", name: "stray", reason: "NO_MATCHING_DEPLOYMENT" }];
    const page = boot(() => data);
    await page.load();

    assert.match(region(page.nodes).children[0].text(), /1 unresolved/);
    assert.match(page.nodes['[data-role="source-badge"]'].textContent, /partial evidence/);
    assert.match(region(page.nodes).children[0].text(), /Unresolved Pod stray: NO_MATCHING_DEPLOYMENT/);
});

test("an empty cluster shows no cards, an explicit headline and the worker count", async () => {
    const page = boot(() => overview({ status: "empty", namespaces: [], totals: { workerNodes: 3, services: 0, unresolved: 0 } }));
    await page.load();

    assert.equal(region(page.nodes).children.length, 0);
    assert.match(headline(page.nodes), /3 worker nodes/);
    assert.match(page.nodes['[data-role="discovery-headline"]'].textContent, /no workloads/i);
});

test("a failure shows an explicit error, no figures and no stale cards", async () => {
    let fail = false;
    const page = boot(() => {
        if (fail) { const error = new Error("down"); error.status = 503; error.code = "CLUSTER_NOT_CONNECTED"; throw error; }
        return overview();
    });
    await page.load();
    assert.equal(region(page.nodes).children.length, 2);

    fail = true;
    await page.load();

    assert.equal(region(page.nodes).children.length, 0);
    assert.equal(headline(page.nodes), "Cluster inventory unavailable");
    assert.equal(page.nodes['[data-role="source-badge"]'].textContent, "Not showing live data");
    assert.match(page.nodes['[data-role="overview-error-region"]'].text(), /CLUSTER_NOT_CONNECTED/);
});

test("untrusted names are rendered as text, never as markup", async () => {
    const data = overview();
    data.namespaces[0].namespace = "<img src=x onerror=alert(1)>";
    const page = boot(() => data);
    await page.load();

    const heading = region(page.nodes).children[0].find((node) => node.tagName === "strong");
    assert.equal(heading.textContent, "<img src=x onerror=alert(1)>");
    assert.equal(heading.children.length, 0);
});
