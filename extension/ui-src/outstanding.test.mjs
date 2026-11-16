import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./outstanding.js", import.meta.url), "utf8");
const flush = async () => { for (let turn = 0; turn < 12; turn += 1) await Promise.resolve(); };

class El {
    constructor(tag) { this.tag = tag; this.children = []; this.dataset = {}; this.hidden = false; this.disabled = false; this.className = ""; this.listeners = {}; }
    set textContent(value) { this.children = value === "" ? [] : [{ textContent: String(value) }]; }
    get textContent() { return this.children.map((child) => child.textContent).join(""); }
    append(...nodes) { this.children.push(...nodes); }
    replaceChildren(...nodes) { this.children = nodes; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    removeEventListener(type, fn) { this.listeners[type] = (this.listeners[type] || []).filter((item) => item !== fn); }
    click() { return Promise.all((this.listeners.click || []).map((fn) => fn({ type: "click" }))); }
}

const roles = ["outstanding-card", "outstanding-status", "outstanding-list", "outstanding-ask", "outstanding-reset", "outstanding-keep", "outstanding-cancel"];
function page() {
    const elements = Object.fromEntries(roles.map((role) => [role, new El(role.includes("list") ? "ul" : "div")]));
    elements["outstanding-card"].hidden = true;
    elements["outstanding-ask"].hidden = true;
    const document = {
        querySelector: (selector) => elements[/\[data-role="([^"]+)"\]/.exec(selector)[1]] || null,
        createElement: (tag) => new El(tag),
    };
    const window = { document, addEventListener: () => {} };
    return { elements, document, window };
}

const change = (extra = {}) => ({
    applicationId: "apply-1", recommendationId: "rec-1", tier: "research", namespace: "shop", status: "APPLIED",
    cluster: { context: "kind-shop", clusterUid: "uid-shop" }, clusterMatch: "same", canReset: true, resetInProgress: false,
    changes: [{ summary: "db: replicas 1 → 2" }], ...extra,
});
const ledger = (changes = [change()]) => ({ connectedClusterUid: "uid-shop", changes, blocking: changes.filter((row) => row.clusterMatch !== "other" && row.status === "APPLIED"), resetInProgress: changes.some((row) => row.resetInProgress) });

function run(initial, request) {
    const p = page();
    const calls = [];
    const fetch = async (url, init = {}) => {
        calls.push({ url, init });
        const answer = request ? await request(url, init, calls.length) : { status: initial.status || 200, body: initial.body || initial };
        if (answer instanceof Error) throw answer;
        return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body };
    };
    vm.runInNewContext(source, { window: p.window, document: p.document, fetch, Promise, JSON, Error, Number, Boolean, Array, Object, String, encodeURIComponent, setTimeout });
    return { ...p, calls, api: p.window.__resilienceOutstanding };
}

test("the reconnect ledger shows every recorded change, including one belonging to another cluster", async () => {
    const other = change({ applicationId: "apply-2", recommendationId: "rec-2", clusterMatch: "other", status: "KEPT", canReset: false, changes: [{ summary: "web: spread none → must spread" }] });
    const p = run(ledger([change(), other]));
    await flush();
    assert.equal(p.elements["outstanding-card"].hidden, false);
    assert.equal(p.elements["outstanding-list"].children.length, 2);
    assert.match(p.elements["outstanding-list"].children[0].textContent, /db: replicas 1 → 2/);
    assert.match(p.elements["outstanding-list"].children[1].textContent, /another cluster/);
    const choice = p.api.page.beforeDisconnect();
    await flush();
    await p.elements["outstanding-keep"].click();
    assert.equal(await choice, "proceed-keep");
});

test("Cancel leaves the connection decision untouched and the next click can ask again", async () => {
    const p = run(ledger());
    await flush();
    const choice = p.api.page.beforeDisconnect();
    await flush();
    assert.equal(p.elements["outstanding-ask"].hidden, false);
    await p.elements["outstanding-cancel"].click();
    assert.equal(await choice, "cancel");
    assert.equal(p.calls.filter((call) => call.init.method === "DELETE").length, 0);
    const again = p.api.page.beforeDisconnect();
    await flush();
    await p.elements["outstanding-cancel"].click();
    assert.equal(await again, "cancel");
});

test("Keep is an explicit proceed-keep decision and never calls Reset", async () => {
    const p = run(ledger());
    await flush();
    const choice = p.api.page.beforeDisconnect();
    await flush();
    await p.elements["outstanding-keep"].click();
    assert.equal(await choice, "proceed-keep");
    assert.equal(p.calls.filter((call) => call.init.method === "POST").length, 0);
});

test("Reset and disconnect resets each tier, waits for the ledger to settle, then proceeds", async () => {
    let reset = false;
    const rows = [change(), change({ applicationId: "apply-2", recommendationId: "rec-2", tier: "production" })];
    const p = run(null, async (url, init) => {
        if (init.method === "POST") { reset = true; return { status: 200, body: {} }; }
        return { status: 200, body: reset ? ledger([]) : ledger(rows) };
    });
    await flush();
    const choice = p.api.page.beforeDisconnect();
    await flush();
    await p.elements["outstanding-reset"].click();
    assert.equal(await choice, "proceed");
    const posts = p.calls.filter((call) => call.init.method === "POST");
    assert.deepEqual(posts.map((call) => call.url), ["/api/recommendations/declared/rec-1/reset", "/api/recommendations/rec-2/reset"]);
    assert.equal(JSON.parse(posts[0].init.body).resetBy, "Reset and disconnect");
});

test("a refused reset leaves the cluster connected and shows the server's reason", async () => {
    const p = run(null, async (url, init) => init.method === "POST"
        ? { status: 409, body: { detail: { code: "EXTERNAL_CHANGE", message: "db replicas were changed outside the application" } } }
        : { status: 200, body: ledger() });
    await flush();
    const choice = p.api.page.beforeDisconnect();
    await flush();
    await p.elements["outstanding-reset"].click();
    assert.equal(await choice, "cancel");
    assert.match(p.elements["outstanding-status"].textContent, /changed outside the application/);
    assert.equal(p.calls.filter((call) => call.init.method === "DELETE").length, 0);
});

test("a reset rollout in progress offers neither Reset nor Keep", async () => {
    const p = run(ledger([change({ status: "RESET", resetInProgress: true, canReset: false })]));
    await flush();
    const choice = p.api.page.beforeDisconnect();
    await flush();
    assert.equal(p.elements["outstanding-reset"].disabled, true);
    assert.equal(p.elements["outstanding-keep"].disabled, true);
    await p.elements["outstanding-cancel"].click();
    assert.equal(await choice, "cancel");
});

test("a missing or unreadable ledger never turns into permission to disconnect", async () => {
    const p = run({ status: 503, body: { detail: { code: "CHANGE_LEDGER_UNAVAILABLE", message: "The ledger could not be read" } } });
    await flush();
    assert.equal(await p.api.page.beforeDisconnect(), "cancel");
    assert.match(p.elements["outstanding-status"].textContent, /could not be read/);
});
