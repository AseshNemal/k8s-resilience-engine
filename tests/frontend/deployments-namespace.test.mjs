// The Deployment Topology page's namespace: which one it shows, how it is added to requests, and that keeping it
// in the URL leaves Docker Desktop's own query parameters alone.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(new URL("../../app/static/js/components/deployments_namespace.js", import.meta.url), "utf8");

function load(href = "http://localhost/components/deployments") {
    const replaced = [];
    const location = new URL(href);
    const window = {
        location,
        history: { replaceState: (_state, _title, url) => replaced.push(url) },
    };
    vm.runInNewContext(script, { window, URLSearchParams, encodeURIComponent, console });
    return { ns: window.DeploymentsNamespace, replaced };
}

const row = (namespace, services) => ({ namespace, graph: { services: Array.from({ length: services }, (_, i) => ({ name: `s${i}` })) } });

test("it shows the namespace with the most services, so Bookinfo is found without typing", () => {
    const { ns } = load();
    assert.equal(ns.choose([row("default", 0), row("bookinfo", 4), row("placement-drift-exp", 3)], null), "bookinfo");
});

test("a namespace named in the URL wins when the cluster has it", () => {
    const { ns } = load();
    assert.equal(ns.choose([row("bookinfo", 4), row("placement-drift-exp", 3)], "placement-drift-exp"), "placement-drift-exp");
});

test("a namespace in the URL that the cluster does not have is not used", () => {
    const { ns } = load();
    assert.equal(ns.choose([row("bookinfo", 4)], "gone"), "bookinfo");
});

test("with no namespace holding workloads it falls back to default", () => {
    const { ns } = load();
    assert.equal(ns.choose([row("default", 0)], null), "default");
    assert.equal(ns.choose([], null), "default");
});

test("requests carry the namespace, encoded, with ? or & as needed", () => {
    const { ns } = load();
    ns.set("bookinfo");
    assert.equal(ns.withNamespace("/api/deployments/discovery"), "/api/deployments/discovery?namespace=bookinfo");
    assert.equal(ns.withNamespace("/api/x?source=live"), "/api/x?source=live&namespace=bookinfo");
    assert.equal(ns.withNamespace("/api/x", "a b"), "/api/x?namespace=a%20b");
});

test("the choice is kept in the URL without dropping Docker Desktop's own query parameters", () => {
    const { ns, replaced } = load("file:///C:/ext/ui/deployments.html?hasBackend=true&extensionName=k8s#drift");
    assert.equal(ns.requested(), null);
    ns.set("bookinfo");
    assert.equal(ns.get(), "bookinfo");
    assert.equal(replaced.length, 1);
    const kept = new URL(replaced[0], "file:///");
    assert.equal(kept.searchParams.get("hasBackend"), "true");
    assert.equal(kept.searchParams.get("extensionName"), "k8s");
    assert.equal(kept.searchParams.get("namespace"), "bookinfo");
    assert.equal(kept.hash, "#drift");
});

test("the namespace named in the URL is read back", () => {
    const { ns } = load("http://localhost/components/deployments?namespace=bookinfo");
    assert.equal(ns.requested(), "bookinfo");
});
