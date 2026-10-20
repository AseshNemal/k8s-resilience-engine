// Logic tests for chrome.js (cluster chip / Overview panel wording). Run: node --test extension/ui-src/
// Dates are shown in the viewer's local time, so pin the zone to keep these assertions machine-independent.
process.env.TZ = "UTC";

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./chrome.js", import.meta.url), "utf8");
const window = {}; // no document: the script exposes its helpers and stops before touching the DOM
vm.runInNewContext(source, { window, Date, Number, Math, String });
const { formatExpiry, describeCluster } = window.__resilienceChrome;

const NOW = new Date("2026-10-18T12:00:00Z");

test("expiry is shown as a date with a relative hint", () => {
    assert.equal(formatExpiry("2026-10-25T12:00:00Z", NOW), "Oct 25, 2026 (in 7 days)");
    assert.equal(formatExpiry("2026-10-18T20:00:00Z", NOW), "Oct 18, 2026 (in 8 hours)");
    assert.equal(formatExpiry("2026-10-18T13:00:00Z", NOW), "Oct 18, 2026 (in 1 hour)");
});

test("a past expiry says expired, and a missing or invalid one yields nothing", () => {
    assert.match(formatExpiry("2026-10-10T08:00:00Z", NOW), /expired/);
    assert.equal(formatExpiry(null, NOW), null);
    assert.equal(formatExpiry("not a date", NOW), null);
});

test("not connected: says the results are sample data and offers to connect", () => {
    const info = describeCluster({ connected: false }, NOW);
    assert.equal(info.state, "none");
    assert.equal(info.chip, "Not connected");
    assert.match(info.detail, /sample data/);
    assert.equal(info.action, "Connect cluster");
});

test("connected: read-only, names the server, and never claims the overview is live", () => {
    const info = describeCluster({ connected: true, server: "host.docker.internal:58227", expiresAt: "2026-10-25T12:00:00Z", expired: false }, NOW);
    assert.equal(info.state, "connected");
    assert.equal(info.chip, "Connected · read-only");
    assert.match(info.title, /host\.docker\.internal:58227/);
    assert.match(info.detail, /expires Oct 25, 2026 \(in 7 days\)/);
    assert.match(info.detail, /Overview and the research pages show sample data/);
    assert.match(info.detail, /Deployment topology can show your live cluster/);
});

test("connected without an expiry still reads correctly", () => {
    const info = describeCluster({ connected: true, server: "h:1", expiresAt: null, expired: false }, NOW);
    assert.doesNotMatch(info.detail, /expires/);
    assert.match(info.detail, /^Read-only access\./);
});

test("an expired credential is flagged and offers renewal", () => {
    const info = describeCluster({ connected: true, server: "h:1", expiresAt: "2026-10-10T08:00:00Z", expired: true }, NOW);
    assert.equal(info.state, "expired");
    assert.equal(info.chip, "Credential expired");
    assert.equal(info.action, "Renew credential");
});

test("no answer from the backend is reported as unavailable, not as disconnected", () => {
    const info = describeCluster(null, NOW);
    assert.equal(info.state, "error");
    assert.equal(info.chip, "Backend unavailable");
    assert.equal(info.action, null);
});
