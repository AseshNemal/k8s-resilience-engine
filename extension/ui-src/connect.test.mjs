import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./connect.js", import.meta.url), "utf8");
const flush = async () => { for (let turn = 0; turn < 12; turn += 1) await Promise.resolve(); };
class El {
    constructor() { this.dataset = {}; this.hidden = false; this.disabled = false; this.children = []; this.listeners = {}; this.files = []; this.value = ""; }
    set textContent(value) { this.children = value === "" ? [] : [{ textContent: String(value) }]; }
    get textContent() { return this.children.map((child) => child.textContent).join(""); }
    replaceChildren() { this.children = []; }
    append(...nodes) { this.children.push(...nodes); }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    async click() { for (const fn of this.listeners.click || []) await fn(); }
}

async function run(choice, deleteAnswer = { status: 200, body: { connected: false } }) {
    const names = ["connect-status", "connect-title", "connect-summary", "connect-details", "connect-message", "connect-disconnect", "connect-file", "connect-filename", "connect-submit", "connect-server", "connect-credential", "connect-expiry"];
    const elements = Object.fromEntries(names.map((name) => [name, new El()]));
    const document = {
        querySelector: (selector) => elements[/\[data-role="([^"]+)"\]/.exec(selector)[1]] || null,
        createTextNode: (text) => ({ textContent: text }),
        createElement: () => new El(),
    };
    const calls = [];
    const fetch = async (url, init = {}) => {
        calls.push({ url, init });
        const answer = init.method === "DELETE" ? deleteAnswer : { status: 200, body: { connected: true, server: "https://cluster", credential: "managed", managed: true } };
        return { ok: answer.status >= 200 && answer.status < 300, status: answer.status, json: async () => answer.body };
    };
    const window = { __resilienceOutstanding: { page: { beforeDisconnect: async () => choice } }, addEventListener: () => {} };
    vm.runInNewContext(source, { window, document, fetch, Promise, JSON, Date, Error, Boolean, String, Object, Array, RegExp });
    await flush();
    return { elements, calls };
}

test("Disconnect uses the explicit Keep decision in the guarded backend call", async () => {
    const p = await run("proceed-keep");
    await p.elements["connect-disconnect"].click();
    assert.equal(p.calls.at(-1).url, "/_extension/cluster?outstanding=keep");
    assert.equal(p.calls.at(-1).init.method, "DELETE");
    assert.match(p.elements["connect-message"].textContent, /changes kept/);
});

test("an ordinary or completed-reset Disconnect uses the original backend call", async () => {
    const p = await run("proceed");
    await p.elements["connect-disconnect"].click();
    assert.equal(p.calls.at(-1).url, "/_extension/cluster");
});

test("Cancel never calls DELETE", async () => {
    const p = await run("cancel");
    await p.elements["connect-disconnect"].click();
    assert.equal(p.calls.filter((call) => call.init.method === "DELETE").length, 0);
    assert.match(p.elements["connect-message"].textContent, /still connected/);
});

test("structured backend refusals show their message instead of only HTTP 409", async () => {
    const p = await run("proceed", { status: 409, body: { detail: { code: "OUTSTANDING_CHANGES", message: "Reset it first, or disconnect keeping it." } } });
    await p.elements["connect-disconnect"].click();
    assert.match(p.elements["connect-message"].textContent, /Reset it first/);
    assert.doesNotMatch(p.elements["connect-message"].textContent, /HTTP 409/);
});
