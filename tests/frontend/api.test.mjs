// Logic tests for app/static/js/api.js: structured backend errors must reach the caller.
// Run with: node --test tests/frontend/api.test.mjs
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/api.js", import.meta.url), "utf8");

function load(response) {
    const window = {};
    vm.runInNewContext(source, {
        window, console, AbortController, setTimeout, clearTimeout,
        fetch: async () => response,
    });
    return window.ResilienceApi;
}

const reply = (status, body) => ({ ok: status < 400, status, json: async () => body });

test("a structured evidence error keeps its status, code, message and details", async () => {
    const api = load(reply(409, { detail: { code: "TOPOLOGY_INCOMPLETE", message: "not enough", details: { services: [] } } }));
    await assert.rejects(api.fetchJson("/x"), (error) => {
        assert.equal(error.status, 409);
        assert.equal(error.code, "TOPOLOGY_INCOMPLETE");
        assert.equal(error.message, "not enough");
        assert.deepEqual({ ...error.details }, { services: [] });
        return true;
    });
});

test("a plain error body keeps the original HTTP-status message and has no code", async () => {
    const api = load(reply(404, { detail: "Not Found" }));
    await assert.rejects(api.fetchJson("/x"), (error) => {
        assert.equal(error.message, "Request failed with HTTP 404");
        assert.equal(error.code, undefined);
        assert.equal(error.status, 404);
        return true;
    });
});

test("an unreadable error body still rejects with the HTTP status", async () => {
    const api = load({ ok: false, status: 502, json: async () => { throw new Error("not json"); } });
    await assert.rejects(api.fetchJson("/x"), /HTTP 502/);
});

test("a successful response is returned unchanged", async () => {
    const api = load(reply(200, { a: 1 }));
    assert.deepEqual({ ...(await api.fetchJson("/x")) }, { a: 1 });
});
