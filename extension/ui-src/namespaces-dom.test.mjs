// Runs namespaces.js's real browser wiring against a minimal DOM stand-in (no DOM library is installed).
// It shows what a person would see: suggestions in the namespace box, the Connect page's list, and the
// explicit failure, empty and incomplete states, with names from the cluster written as text only.
// Run: node --test extension/ui-src/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./namespaces.js", import.meta.url), "utf8");

class El {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.attrs = {}; this.className = ""; this.hidden = false; this.text = ""; this.parent = null; this.value = ""; this.label = ""; this.id = ""; }
    set textContent(v) { this.text = String(v); this.children = []; }
    get textContent() { return this.text + this.children.map((c) => c.textContent).join(""); }
    append(...nodes) { for (const n of nodes) { n.parent = this; this.children.push(n); } }
    replaceChildren(...nodes) { this.children = []; this.append(...nodes); }
    setAttribute(k, v) { this.attrs[k] = v; }
    insertAdjacentElement(where, node) {
        assert.equal(where, "afterend");
        node.parent = this.parent;
        this.parent.children.splice(this.parent.children.indexOf(this) + 1, 0, node);
        return node;
    }
    walk() { return [this, ...this.children.flatMap((c) => c.walk())]; }
}

function page({ inputs = 0, apps = false }) {
    const body = new El("body");
    const make = (role, tag = "div") => { const e = new El(tag); e.dataset.role = role; body.append(e); return e; };
    const namespaceInputs = Array.from({ length: inputs }, () => make("namespace", "input"));
    const parts = apps ? { card: make("apps-card"), status: make("apps-status"), list: make("apps-list", "ul") } : {};
    if (parts.card) parts.card.hidden = true;
    const handlers = { document: {}, window: {} };
    const document = {
        body, hidden: false,
        createElement: (tag) => new El(tag),
        querySelector: (selector) => document.querySelectorAll(selector)[0] || null,
        querySelectorAll: (selector) => {
            const role = /\[data-role="([^"]+)"\]/.exec(selector)[1];
            return body.walk().filter((e) => e.dataset.role === role);
        },
        addEventListener: (type, fn) => { handlers.document[type] = fn; },
    };
    const window = { document, addEventListener: (type, fn) => { handlers.window[type] = fn; } };
    return { body, document, window, handlers, inputs: namespaceInputs, ...parts };
}

async function run(p, response) {
    const requests = [];
    const timers = [];
    const fetch = async (url, init) => {
        requests.push({ url, init });
        const r = typeof response === "function" ? response() : response;
        return { status: r.status, json: async () => r.body };
    };
    vm.runInNewContext(source, {
        window: p.window, document: p.document, fetch, Math, Date, JSON, Object, Array, Promise, String, Number, Boolean, Error, Set,
        setTimeout: (fn, ms) => { timers.push({ fn, ms }); return timers.length; }, clearTimeout: () => {},
    });
    if (p.handlers.document.DOMContentLoaded) await p.handlers.document.DOMContentLoaded();   // absent when the page has nothing to fill in
    return { requests, timers };
}

const svc = (name, ready = 1, declared = 1) => ({ name, readyReplicas: ready, declaredReplicas: declared });
const ns = (namespace, services, extra = {}) => ({ status: "ok", namespace, declaredState: "unavailable", unresolved: [], serviceEvidence: {}, graph: { services }, ...extra });
const ok = (...rows) => ({ status: 200, body: { status: "ok", capturedAt: "x", namespaces: rows, excludedNamespaces: ["kube-system"], totals: {} } });

test("the namespace boxes get the cluster's namespaces as suggestions, and a line saying what was read", async () => {
    const p = page({ inputs: 2 });
    const { requests } = await run(p, ok(ns("bookinfo", [svc("productpage"), svc("reviews", 1, 2)]), ns("shop", [svc("cart")])));

    assert.deepEqual(requests.map((r) => r.url), ["/api/deployments/discovery/all"]);
    assert.equal(requests[0].init, undefined, "a plain read: no method, no body");
    const list = p.body.walk().find((e) => e.tag === "datalist");
    assert.deepEqual(list.children.map((o) => [o.value, o.textContent]), [["bookinfo", "bookinfo · 2 services"], ["shop", "shop · 1 service"]]);
    for (const input of p.inputs) assert.equal(input.attrs.list, list.id);
    const hints = p.body.walk().filter((e) => e.dataset.role === "namespace-hint");
    assert.equal(hints.length, 2);
    assert.match(hints[0].textContent, /2 namespaces with applications, 3 services/);
    assert.equal(hints[0].dataset.kind, "ok");
});

test("a page with nothing to fill in never reads the cluster", async () => {
    const p = page({});
    const { requests, timers } = await run(p, ok());
    assert.equal(requests.length, 0);
    assert.equal(timers.length, 0);
    assert.equal(p.handlers.window["resilience-cluster-changed"], undefined);
    assert.equal(p.handlers.document.DOMContentLoaded, undefined, "it does not even register for page events");
});

test("the Connect page lists namespaces with their services and marks incomplete evidence with Member 2's reasons", async () => {
    const p = page({ apps: true });
    await run(p, ok(
        ns("bookinfo", [svc("details"), svc("reviews", 1, 2)]),
        ns("kube-dns-ns", [svc("dns", 0, 2)], { status: "partial", unresolved: [{ reason: "POD_ON_EXCLUDED_NODE" }] }),
    ));

    assert.equal(p.card.hidden, false);
    const items = p.list.children;
    assert.equal(items.length, 2);
    assert.match(items[0].textContent, /bookinfo · 2 services/);
    assert.match(items[0].textContent, /details · 1\/1 ready · reviews · 1\/2 ready/);
    assert.equal(items[0].dataset.incomplete, "false");
    assert.equal(items[1].dataset.incomplete, "true");
    assert.match(items[1].textContent, /incomplete evidence \(POD_ON_EXCLUDED_NODE\)/);
    assert.match(p.status.textContent, /Evidence is incomplete for 1 namespace/);
    assert.equal(p.status.dataset.kind, "incomplete");
});

test("a connected cluster with nothing running is an empty state, not an error and not a made-up list", async () => {
    const p = page({ apps: true, inputs: 1 });
    await run(p, ok());
    assert.equal(p.list.children.length, 0);
    assert.equal(p.status.dataset.kind, "empty");
    assert.match(p.status.textContent, /No namespace holds workloads/);
    assert.equal(p.body.walk().find((e) => e.tag === "datalist").children.length, 0);
});

test("a connection failure is shown in words on every surface and leaves no names behind", async () => {
    const p = page({ apps: true, inputs: 1 });
    await run(p, { status: 503, body: { detail: { code: "CLUSTER_NOT_CONNECTED", message: "x" } } });

    assert.equal(p.status.dataset.kind, "error");
    assert.match(p.status.textContent, /No cluster is connected/);
    assert.equal(p.list.children.length, 0);
    assert.equal(p.body.walk().find((e) => e.tag === "datalist").children.length, 0);
    assert.match(p.body.walk().find((e) => e.dataset.role === "namespace-hint").textContent, /Connect page/);
});

test("names from the cluster are written as text, never as markup", async () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const p = page({ apps: true, inputs: 1 });
    await run(p, ok(ns(hostile, [svc("<b>svc</b>")])));

    const item = p.list.children[0];
    assert.equal(item.children[0].tag, "strong");
    assert.equal(item.children[0].textContent, hostile, "shown literally");
    assert.ok(!p.body.walk().some((e) => e.tag === "img" || e.tag === "b"), "no element was created from cluster text");
    assert.equal(p.body.walk().find((e) => e.tag === "option").value, hostile);
});

test("the connection announcing a change reads again at once after a failure, and the next minute is scheduled", async () => {
    let answer = { status: 503, body: { detail: { code: "CLUSTER_NOT_CONNECTED" } } };
    const p = page({ apps: true });
    const { requests, timers } = await run(p, () => answer);
    assert.equal(p.status.dataset.kind, "error");
    assert.equal(timers.at(-1).ms, 15000, "a failure is retried soon");

    answer = ok(ns("shop", [svc("cart")]));
    await p.handlers.window["resilience-cluster-changed"]();

    assert.equal(requests.length, 2);
    assert.equal(p.status.dataset.kind, "ok");
    assert.equal(timers.at(-1).ms, 60000);
});

test("coming back to a visible tab reads again only when the list is stale", async () => {
    const p = page({ apps: true });
    const { requests } = await run(p, ok(ns("shop", [svc("cart")])));
    p.document.hidden = false;
    await p.handlers.document.visibilitychange();
    assert.equal(requests.length, 1, "just read: nothing to refresh");
});

test("when the cluster stops answering, names it listed a minute ago are removed from every surface", async () => {
    let answer = ok(ns("shop", [svc("cart")]));
    const p = page({ apps: true, inputs: 1 });
    await run(p, () => answer);
    assert.equal(p.list.children.length, 1);
    assert.equal(p.body.walk().find((e) => e.tag === "datalist").children.length, 1);

    answer = { status: 503, body: { detail: { code: "CLUSTER_CONNECTION_FAILED" } } };
    await p.handlers.window["resilience-cluster-changed"]();     // not stale yet, and still good: no read
    assert.equal(p.list.children.length, 1);
    await window_refresh(p);

    assert.equal(p.list.children.length, 0, "no stale applications");
    assert.equal(p.body.walk().find((e) => e.tag === "datalist").children.length, 0, "no stale suggestions");
    assert.equal(p.status.dataset.kind, "error");
});

// The page's refresh (a timer, or the person coming back) goes through the same refresher the wiring created.
async function window_refresh(p) { await p.window.__resilienceRefresher.refresh(); }

test("replica counts the cluster did not report are shown as not reported, never as zero", async () => {
    const p = page({ apps: true });
    await run(p, ok(ns("x", [{ name: "mystery" }])));
    assert.match(p.list.children[0].textContent, /mystery · replicas not reported/);
    assert.doesNotMatch(p.list.children[0].textContent, /0\/0|0 ready/);
});
