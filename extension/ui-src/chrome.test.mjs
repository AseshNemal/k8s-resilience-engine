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

test("not connected: says pages show an error instead of data and offers to connect", () => {
    const info = describeCluster({ connected: false }, NOW);
    assert.equal(info.state, "none");
    assert.equal(info.chip, "Not connected");
    assert.match(info.detail, /error instead of data/);
    assert.doesNotMatch(info.detail, /sample/i);
    assert.equal(info.action, "Connect cluster");
});

test("connected: read-only, names the server, and says pages read the connected cluster", () => {
    const info = describeCluster({ connected: true, server: "host.docker.internal:58227", expiresAt: "2026-10-25T12:00:00Z", expired: false }, NOW);
    assert.equal(info.state, "connected");
    assert.equal(info.chip, "Connected · read-only");
    assert.match(info.title, /host\.docker\.internal:58227/);
    assert.match(info.detail, /expires Oct 25, 2026 \(in 7 days\)/);
    assert.match(info.detail, /Overview and Deployment topology show your connected cluster/);
    assert.doesNotMatch(info.detail, /sample/i);
});

test("connected without an expiry still reads correctly", () => {
    const info = describeCluster({ connected: true, server: "h:1", expiresAt: null, expired: false }, NOW);
    assert.doesNotMatch(info.detail, /expires/);
    assert.match(info.detail, /^Read-only access\./);
});

const MANAGED_EXPIRED = { connected: true, server: "h:1", expiresAt: "2026-10-10T08:00:00Z", expired: true, managed: true, context: "kind-x" };
const MANAGED_LIVE = { connected: true, server: "h:1", expiresAt: "2026-10-25T12:00:00Z", expired: false, managed: true, context: "kind-x" };

test("an expired helper-managed credential is shown as renewing by itself, with no button to press", () => {
    const info = describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "renewing", background: "on" });
    assert.equal(info.state, "renewing");
    assert.equal(info.chip, "Renewing…");
    assert.equal(info.action, null, "renewal is automatic, so there is nothing to click");
    assert.match(info.title, /renewing the credential automatically/i);
    assert.doesNotMatch(info.detail, /expire/i, "no expiry text for a credential that renews itself");
    assert.doesNotMatch(info.detail, /sample/i);
});

test("while a renewal keeps failing, the reason is shown but there is still nothing to click", () => {
    const info = describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "retrying", state: "too-slow", message: "Docker Desktop is responding very slowly.", background: "on" });
    assert.equal(info.state, "renewing");
    assert.match(info.detail, /Docker Desktop is responding very slowly/);
    assert.equal(info.action, null);
});

test("only a problem a person can fix asks for their attention, and then it points at the connection page", () => {
    const info = describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "attention", state: "authorization-revoked", message: "The extension's access was removed from the cluster." });
    assert.equal(info.state, "attention");
    assert.match(info.detail, /access was removed/);
    assert.equal(info.action, "Manage connection");
});

test("a managed credential that has expired and has no background job offers to turn it on once", () => {
    const info = describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "renewing", background: "off" });
    assert.equal(info.action, "Turn on background renewal");
    assert.equal(describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "renewing", background: "stale" }).action, "Turn on background renewal");
    assert.equal(describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "renewing", background: "" }).action, null, "unknown is not off: never nag on a guess");
    assert.equal(describeCluster(MANAGED_EXPIRED, NOW, true, { phase: "renewing", background: "unsupported" }).action, null);
});

test("a live managed credential shows no expiry and says it renews itself", () => {
    const info = describeCluster(MANAGED_LIVE, NOW, true, { phase: "ready", background: "on" });
    assert.equal(info.state, "connected");
    assert.doesNotMatch(info.detail, /expires/);
    assert.match(info.detail, /renews itself/);
    assert.match(info.detail, /background/i);
    assert.equal(info.action, "Manage connection");
});

test("a live managed credential without the background job explains the gap and offers to close it", () => {
    const info = describeCluster(MANAGED_LIVE, NOW, true, { phase: "ready", background: "off" });
    assert.match(info.detail, /Docker Desktop is closed/);
    assert.equal(info.action, "Turn on background renewal");
});

test("an expired hand-added credential cannot renew itself, so it offers automatic setup", () => {
    const status = { connected: true, server: "h:1", expiresAt: "2026-10-10T08:00:00Z", expired: true, managed: false };
    const info = describeCluster(status, NOW, true);
    assert.equal(info.state, "expired");
    assert.equal(info.action, "Set up automatic renewal");
    assert.match(info.detail, /added by hand/);
    assert.match(info.detail, /renews itself/);
});

test("an expired hand-added credential without automatic setup offers a replacement, not renewal", () => {
    const status = { connected: true, server: "h:1", expiresAt: "2026-10-10T08:00:00Z", expired: true, managed: false };
    const info = describeCluster(status, NOW, false);
    assert.equal(info.action, "Replace credential");
    assert.doesNotMatch(info.detail, /renews itself/);
});

test("a live hand-added credential warns it will not renew and offers automatic setup", () => {
    const status = { connected: true, server: "h:1", expiresAt: "2026-10-25T12:00:00Z", expired: false, managed: false };
    const info = describeCluster(status, NOW, true);
    assert.equal(info.state, "connected");
    assert.equal(info.action, "Set up automatic renewal");
    assert.match(info.detail, /will not renew/);
    // the unmanaged wording only appears where automatic setup exists
    assert.equal(describeCluster(status, NOW, false).action, "Manage connection");
});

test("no answer from the backend is reported as unavailable, not as disconnected", () => {
    const info = describeCluster(null, NOW);
    assert.equal(info.state, "error");
    assert.equal(info.chip, "Backend unavailable");
    assert.equal(info.action, null);
});

// ---- a cluster the person approved earlier that is not connected right now

const APPROVED = { context: "docker-desktop", server: "127.0.0.1:50345", caSha256: "ab".repeat(32), disconnectedByUser: false };

test("an approved cluster that is being reconnected says so and needs nothing from the person", () => {
    const info = describeCluster({ connected: false, approved: APPROVED }, NOW, true, { phase: "reconnecting" });
    assert.equal(info.state, "renewing");
    assert.equal(info.chip, "Reconnecting…");
    assert.match(info.title, /docker-desktop/);
    assert.match(info.detail, /approved this cluster earlier/);
    assert.equal(info.action, null);
});

test("while a reconnect keeps failing the reason is shown, still with nothing to click", () => {
    const info = describeCluster({ connected: false, approved: APPROVED }, NOW, true, { phase: "retrying", message: "The cluster is not reachable." });
    assert.equal(info.state, "renewing");
    assert.match(info.detail, /not reachable/);
    assert.equal(info.action, null);
});

test("a reconnect that needs the person asks for their attention and points at the connection page", () => {
    const info = describeCluster({ connected: false, approved: APPROVED }, NOW, true, { phase: "attention", message: "The cluster behind this context is not the one you approved." });
    assert.equal(info.state, "attention");
    assert.equal(info.chip, "Needs your attention");
    assert.match(info.detail, /not the one you approved/);
    assert.equal(info.action, "Manage connection");
});

test("a cluster the person disconnected is shown as not connected and says it will not reconnect by itself", () => {
    const info = describeCluster({ connected: false, approved: { ...APPROVED, disconnectedByUser: true } }, NOW, true, { phase: "needs-setup" });
    assert.equal(info.state, "none");
    assert.equal(info.chip, "Not connected");
    assert.match(info.detail, /disconnected/);
    assert.match(info.detail, /not reconnect/i);
    assert.equal(info.action, "Connect cluster");
});

test("without automatic setup an approved cluster is never described as reconnecting", () => {
    const info = describeCluster({ connected: false, approved: APPROVED }, NOW, false, null);
    assert.equal(info.state, "none");
    assert.equal(info.action, "Connect cluster");
});

test("a cluster that was never approved is described exactly as before", () => {
    const info = describeCluster({ connected: false }, NOW, true, { phase: "needs-setup" });
    assert.equal(info.chip, "Not connected");
    assert.doesNotMatch(info.detail, /approved this cluster earlier|disconnected/);
});
