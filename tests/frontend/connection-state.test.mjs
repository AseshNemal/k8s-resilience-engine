// Logic tests for app/static/js/connection-state.js. Run with: node --test tests/frontend/
// The module only maps API errors / discovery results to display states; it never invents data.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../app/static/js/connection-state.js", import.meta.url), "utf8");

function load() {
    const window = {};
    vm.runInNewContext(source, { window, console });
    return window.ResilienceConnection;
}

const api = load();

function apiError(status, code, message, details) {
    const error = new Error(message || `Request failed with HTTP ${status}`);
    error.status = status;
    error.code = code;
    error.details = details;
    return error;
}

test("every cluster failure code maps to its own state and never claims success", () => {
    const cases = {
        CLUSTER_NOT_CONNECTED: "not-connected",
        CLUSTER_CONNECTION_FAILED: "connection-failed",
        CLUSTER_AUTH_FAILED: "access-denied",
        CLUSTER_FORBIDDEN: "access-denied",
        DISCOVERY_FAILED: "discovery-failed",
        INVALID_NAMESPACE: "invalid-input",
        TOPOLOGY_INCOMPLETE: "insufficient-evidence",
        WORKFLOW_EVIDENCE_UNAVAILABLE: "evidence-unavailable",
        RECOMMENDATION_EVIDENCE_UNAVAILABLE: "evidence-unavailable",
        DECLARED_STATE_UNAVAILABLE: "evidence-unavailable",
        SNAPSHOT_PROVENANCE_UNVERIFIED: "evidence-unavailable",
        APPROVAL_PROVENANCE_UNVERIFIED: "evidence-unavailable",
    };
    for (const [code, state] of Object.entries(cases)) {
        const described = api.describeError(apiError(502, code, "backend text"));
        assert.equal(described.state, state, code);
        assert.equal(described.code, code);
        assert.notEqual(described.state, "ok");
    }
});

test("a client-side input error is shown as an input problem, not a backend outage", () => {
    const described = api.describeError(apiError(0, "INVALID_INPUT", "Enter the frozen workflow ID."));
    assert.equal(described.state, "invalid-input");
    assert.match(described.message, /frozen workflow ID/);
    assert.equal(described.retryable, false);
});

test("the backend's own sanitised message is shown when it provides one", () => {
    const described = api.describeError(apiError(502, "CLUSTER_FORBIDDEN", "The credential is not allowed to list pods."));
    assert.match(described.message, /not allowed to list pods/);
});

test("a missing backend answer is distinguished from a cluster failure", () => {
    const described = api.describeError(new Error("Request timed out after 8000 ms"));
    assert.equal(described.state, "backend-unreachable");
    assert.equal(described.code, null);
});

test("an unknown coded error is a generic error that still carries its code", () => {
    const described = api.describeError(apiError(500, "SOMETHING_NEW", "odd"));
    assert.equal(described.state, "error");
    assert.equal(described.code, "SOMETHING_NEW");
});

test("insufficient evidence lists which services lack placement evidence", () => {
    const described = api.describeError(apiError(409, "TOPOLOGY_INCOMPLETE", "msg", {
        services: [{ service: "db", reasons: ["UNSCHEDULED_PODS"] }],
    }));
    assert.deepEqual([...described.lines], ["db: UNSCHEDULED_PODS"]); // copied: arrays from the vm context have another prototype
});

function discovery(overrides) {
    return {
        status: "ok",
        namespace: "shop",
        capturedAt: "2026-01-01T00:00:00Z",
        cluster: { connectionFingerprint: "abcdef0123456789", identityVerified: false },
        declaredState: "unavailable",
        warnings: [],
        unresolved: [],
        graph: { nodes: [{ name: "w1" }], services: [{ name: "web" }] },
        ...overrides,
    };
}

test("complete discovery is labelled live and complete", () => {
    const described = api.describeDiscovery(discovery({}));
    assert.equal(described.state, "ok");
    assert.equal(described.badge.variant, "success");
    assert.match(described.badge.text, /Live/);
});

test("a connected cluster with no workloads is a valid empty state, not an error", () => {
    const described = api.describeDiscovery(discovery({
        status: "empty",
        graph: { nodes: [{ name: "w1" }], services: [] },
        warnings: [{ code: "NAMESPACE_EXISTENCE_UNVERIFIED", message: "could not verify" }],
    }));
    assert.equal(described.state, "empty");
    assert.equal(described.badge.variant, "info");
    assert.ok(described.notes.some((n) => /could not verify/.test(n)));
    assert.ok(!described.notes.some((n) => /error|failed/i.test(n)));
});

test("partial discovery is never presented as complete", () => {
    const described = api.describeDiscovery(discovery({
        status: "partial",
        unresolved: [{ kind: "Pod", name: "stray-1", reason: "NO_MATCHING_DEPLOYMENT" }],
    }));
    assert.equal(described.state, "partial");
    assert.equal(described.badge.variant, "warning");
    assert.doesNotMatch(described.badge.text, /complete/i);
    assert.ok(described.notes.some((n) => /stray-1/.test(n)));
});

test("the connection fingerprint is never presented as proof of cluster identity", () => {
    const described = api.describeDiscovery(discovery({}));
    const text = [described.provenance, ...described.notes].join(" ");
    assert.match(described.provenance, /not proof/i);
    assert.doesNotMatch(text, /verified cluster|cluster identity verified/i);
});

test("declared-state absence is stated, not hidden", () => {
    const described = api.describeDiscovery(discovery({}));
    assert.ok(described.notes.some((n) => /declared/i.test(n)));
});

function fakeDocument() {
    const make = (tag) => ({
        tag, children: [], attributes: {}, textContent: "", listeners: {},
        setAttribute(name, value) { this.attributes[name] = value; },
        appendChild(child) { this.children.push(child); return child; },
        addEventListener(type, fn) { this.listeners[type] = fn; },
    });
    return { createElement: make };
}

function walk(node, visit) {
    visit(node);
    (node.children || []).forEach((child) => walk(child, visit));
}

test("rendering puts untrusted text in textContent, never in markup", () => {
    const doc = fakeDocument();
    let rendered = null;
    const container = { removeAttribute() {}, replaceChildren(node) { rendered = node; } };
    const hostile = "<img src=x onerror=alert(1)>";

    api.renderStateCard(container, api.describeError(apiError(502, "DISCOVERY_FAILED", hostile)), { document: doc });

    const texts = [];
    walk(rendered, (n) => texts.push(n.textContent));
    assert.ok(texts.includes(hostile));            // kept as plain text
    assert.equal("innerHTML" in rendered, false);  // no HTML channel is used at all
});

test("retry is offered only when it can help and a handler is given", () => {
    const doc = fakeDocument();
    let rendered = null;
    const container = { removeAttribute() {}, replaceChildren(node) { rendered = node; } };
    let retried = 0;

    api.renderStateCard(container, api.describeError(apiError(502, "CLUSTER_CONNECTION_FAILED")), { document: doc, onRetry: () => { retried++; } });
    const buttons = [];
    walk(rendered, (n) => n.tag === "button" && buttons.push(n));
    assert.equal(buttons.length, 1);
    buttons[0].listeners.click();
    assert.equal(retried, 1);

    api.renderStateCard(container, api.describeError(apiError(422, "INVALID_NAMESPACE")), { document: doc, onRetry: () => {} });
    const none = [];
    walk(rendered, (n) => n.tag === "button" && none.push(n));
    assert.equal(none.length, 0);
});

test("no state helper contains sample or demo wording", () => {
    assert.doesNotMatch(source, /sample data|demo data|Live Cluster \(KinD\)/i);
});
