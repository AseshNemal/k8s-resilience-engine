// The Live Experiments tab's simulation on the connected cluster: which namespace it picks, what it asks the server,
// and that it never shows a result as the cluster's unless the server says the topology was live.
// Run with: node --test tests/frontend/
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const script = readFileSync(
    new URL("../../app/static/js/components/deployments_live_experiments_widget.js", import.meta.url), "utf8");
const flush = () => new Promise((resolve) => setImmediate(resolve));

function makeEnvironment({ overview, benchmarks }) {
    const registry = new Map();
    const node = (key) => {
        if (registry.has(key)) return registry.get(key);
        const n = {
            key, children: [], className: "", textContent: "", style: {}, listeners: {}, dataset: {}, disabled: false,
            hidden: false, value: "",
            setAttribute() {}, removeAttribute() {},
            append(...nodes) { this.children.push(...nodes); },
            appendChild(child) { this.children.push(child); return child; },
            replaceChildren(...nodes) { this.children = [...nodes]; },
            addEventListener(type, fn) { this.listeners[type] = fn; },
            querySelector: (selector) => node(selector),
            querySelectorAll: () => [],
        };
        registry.set(key, n);
        return n;
    };
    const root = node("#tab-live-experiments");
    const document = {
        getElementById: (id) => (id === "tab-live-experiments" ? root : node(`#${id}`)),
        createElement: () => ({ ...node(`new:${Math.random()}`), children: [], style: {} }),
    };
    const calls = [];
    const window = {
        ResilienceApi: {
            fetchJson: async (url) => {
                calls.push(url);
                const { status, body } = await (url.startsWith("/api/deployments/discovery/all") ? overview() : benchmarks(url));
                if (status >= 400) {
                    const detail = body.detail;
                    throw Object.assign(new Error(detail.message), { status, code: detail.code });
                }
                return body;
            },
        },
    };
    // The stored-results half of the tab asks for its own file; it is not under test here.
    const fetch = async () => ({ ok: false, status: 404, json: async () => ({}) });
    const sandbox = { window, document, fetch, console, encodeURIComponent, Math, String, Object };
    vm.createContext(sandbox);
    vm.runInContext(script, sandbox, { filename: "deployments_live_experiments_widget.js" });
    const parts = {
        select: node("[data-role='live-sim-namespace']"),
        run: node("[data-role='live-sim-run']"),
        status: node("[data-role='live-sim-status']"),
        results: node("[data-role='live-sim-results']"),
        body: node("[data-role='live-sim-body']"),
    };
    return { widget: window.DeploymentsLiveExperimentsWidget, calls, parts, node };
}

const ns = (namespace, services) => ({ namespace, graph: { services: Array.from({ length: services }, (_, i) => ({ name: `s${i}`, pods: [] })) } });
const ok = (body) => ({ status: 200, body });
const scenario = (id, extra = {}) => ({
    scenario_id: id, scenario_name: `${id} (live topology)`, status: "COMPLETED", d_rate: 0, delta_e_rate: 0,
    drifted_services: [], transitions: [], error_message: null, topology_source: "live", ...extra,
});
const benchmark = (source = "live", results = [scenario("02_scale_up_stress", { d_rate: 0.25, delta_e_rate: -0.25, drifted_services: ["details"] }), scenario("07_node_drain")]) => ({
    aggregate_metrics: { total_scenarios: 8, completed_count: 8, topology_source: source, mean_d_rate: 0.0312, max_d_rate: 0.25 },
    scenario_results: results,
});

test("it picks the namespace with the most services, so a cluster whose workload is not in 'default' still works", async () => {
    const env = makeEnvironment({
        overview: () => ok({ namespaces: [ns("default", 0), ns("bookinfo", 4), ns("monitoring", 2)] }),
        benchmarks: () => ok(benchmark()),
    });
    await env.widget.loadSimulationNamespaces();

    assert.equal(env.parts.select.value, "bookinfo");
    assert.deepEqual(env.parts.select.children.map((o) => o.textContent),
        ["default · 0 services", "bookinfo · 4 services", "monitoring · 2 services"]);
    assert.equal(env.parts.select.disabled, false);
});

test("it asks the server for the live topology of the chosen namespace, and nothing else", async () => {
    const env = makeEnvironment({ overview: () => ok({ namespaces: [ns("bookinfo", 4)] }), benchmarks: () => ok(benchmark()) });
    await env.widget.loadSimulationNamespaces();

    assert.deepEqual(env.calls, [
        "/api/deployments/discovery/all",
        "/api/deployments/chaos/benchmarks?source=live&namespace=bookinfo",
    ]);
});

test("a live result is shown, and described as a prediction on the real topology", async () => {
    const env = makeEnvironment({ overview: () => ok({ namespaces: [ns("bookinfo", 4)] }), benchmarks: () => ok(benchmark()) });
    await env.widget.loadSimulationNamespaces();

    assert.equal(env.parts.results.hidden, false);
    assert.equal(env.parts.body.children.length, 2);
    const first = env.parts.body.children[0].children.map((cell) => cell.textContent);
    assert.equal(first[0], "02_scale_up_stress", "the '(live topology)' suffix is dropped from the name");
    assert.equal(first[2], "25.0%");
    assert.equal(first[4], "details");
    assert.match(env.parts.status.textContent, /8 of 8 scenarios completed/);
    assert.match(env.parts.status.textContent, /not experimental evidence/);
    assert.match(env.parts.status.textContent, /bookinfo/);
});

test("a response that is not from a live topology is refused, never relabelled as the cluster's", async () => {
    const env = makeEnvironment({ overview: () => ok({ namespaces: [ns("bookinfo", 4)] }), benchmarks: () => ok(benchmark("scripted")) });
    await env.widget.loadSimulationNamespaces();

    assert.equal(env.parts.results.hidden, true);
    assert.equal(env.parts.body.children.length, 0);
    assert.match(env.parts.status.textContent, /did not report a live topology/);
});

test("a namespace with nothing to disrupt says so and suggests another", async () => {
    const env = makeEnvironment({
        overview: () => ok({ namespaces: [ns("empty-one", 1)] }),
        benchmarks: () => ({ status: 409, body: { detail: { code: "TOPOLOGY_HAS_NO_WORKLOADS", message: "The connected cluster has no service with a placed pod in this namespace, so there is nothing to disrupt in a simulation." } } }),
    });
    await env.widget.loadSimulationNamespaces();

    assert.equal(env.parts.results.hidden, true);
    assert.match(env.parts.status.textContent, /nothing to disrupt/);
    assert.match(env.parts.status.textContent, /Choose another namespace/);
});

test("with no cluster connected the tab says so and offers nothing to run", async () => {
    const env = makeEnvironment({
        overview: () => ({ status: 409, body: { detail: { code: "CLUSTER_NOT_CONNECTED", message: "No cluster is connected." } } }),
        benchmarks: () => { throw new Error("must not run without a cluster"); },
    });
    await env.widget.loadSimulationNamespaces();

    assert.equal(env.parts.select.disabled, true);
    assert.equal(env.parts.run.disabled, true);
    assert.deepEqual(env.parts.select.children.map((o) => o.textContent), ["Cluster not available"]);
    assert.match(env.parts.status.textContent, /No cluster is connected/);
    assert.equal(env.calls.length, 1);
});

test("a cluster with no workloads anywhere says so, without running a simulation", async () => {
    const env = makeEnvironment({
        overview: () => ok({ namespaces: [ns("empty", 0)] }),
        benchmarks: () => { throw new Error("must not run"); },
    });
    await env.widget.loadSimulationNamespaces();

    assert.deepEqual(env.parts.select.children.map((o) => o.textContent), ["No namespace with workloads"]);
    assert.match(env.parts.status.textContent, /no namespace with workloads/i);
    assert.equal(env.calls.length, 1);
});

test("changing the namespace runs the simulation for the new namespace", async () => {
    const env = makeEnvironment({ overview: () => ok({ namespaces: [ns("a", 3), ns("b", 2)] }), benchmarks: () => ok(benchmark()) });
    await env.widget.loadSimulationNamespaces();
    assert.equal(env.calls.at(-1), "/api/deployments/chaos/benchmarks?source=live&namespace=a");

    env.parts.select.value = "b";
    await env.widget.runSimulation();

    assert.equal(env.calls.at(-1), "/api/deployments/chaos/benchmarks?source=live&namespace=b");
    assert.match(env.parts.status.textContent, /Namespace b/);
});

test("clicks during a run never run alongside it; they become a single follow-up", async () => {
    const gates = [];
    let active = 0;
    let peak = 0;
    const env = makeEnvironment({
        overview: () => ok({ namespaces: [ns("a", 3)] }),
        benchmarks: async () => {
            active += 1;
            peak = Math.max(peak, active);
            await new Promise((resolve) => gates.push(resolve));
            active -= 1;
            return ok(benchmark());
        },
    });
    env.parts.select.value = "a";

    const first = env.widget.runSimulation();
    await flush();
    env.widget.runSimulation();
    env.widget.runSimulation();
    env.widget.runSimulation();
    await flush();
    assert.equal(gates.length, 1, "only the first run is in flight");

    gates[0]();
    await first;
    await flush();
    assert.equal(gates.length, 2, "the three clicks became one follow-up");
    gates[1]();
    await flush();
    await flush();
    assert.equal(peak, 1, "never two simulations at the same time");
    assert.equal(env.calls.filter((url) => url.includes("chaos/benchmarks")).length, 2);
});
