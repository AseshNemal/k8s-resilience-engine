// The merged workspace discovers only from user-supplied connection settings.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {test} from 'node:test';
import vm from 'node:vm';
const script = readFileSync(new URL('../../app/static/js/components/workflows.js', import.meta.url), 'utf8');
const pollerScript = readFileSync(new URL('../../app/static/js/workflow-discovery-poller.js', import.meta.url), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));
function makeEnvironment({discover, hidden = false, traceService} = {}) {
    const registry = new Map();
    const element = key => {
        if (registry.has(key)) return registry.get(key);
        const node = {children: [], value: '', textContent: '', disabled: false, hidden: false, checked: false,
            dataset: {}, listeners: {}, classList: {toggle() {}},
            setAttribute() {}, addEventListener(type, fn) {this.listeners[type] = fn;},
            replaceChildren(...children) {this.children = children; this.value = '';},
            append(...children) {this.children.push(...children);}, focus() {},
            querySelector: selector => element(selector), querySelectorAll: () => []};
        registry.set(key, node);
        return node;
    };
    const field = name => element('field:' + name);
    const form = element('[data-role="guided-analysis-form"]');
    const offline = element('[data-role="offline-analysis-form"]');
    form.elements = offline.elements = new Proxy({namedItem: field}, {get: (target, key) => target[key] || field(key)});
    form.reportValidity = () => ['traceEndpoint', 'environmentId', 'evidenceTier'].every(key => field(key).value);
    field('traceSourceType').value = 'jaeger';
    field('traceAuthentication').value = 'none';
    field('lookback').value = '1h';
    const listeners = {}, windowListeners = {};
    const document = {hidden, querySelector: element, querySelectorAll: () => [],
        createElement: tag => element('new:' + tag + ':' + Math.random()),
        addEventListener(type, fn) {listeners[type] = fn;}};
    const calls = [];
    const json = (code, body) => ({ok: code < 400, status: code, json: async () => body});
    const fetch = async (url, options = {}) => {
        calls.push([options.method || 'GET', url, options.body]);
        if (url === '/api/workflows/discovery') return discover();
        return json(200, []);
    };
    // A fake clock: timers run only when a test advances it.
    const timers = [];
    let clock = 0;
    const setTimeout = (fn, ms) => { const timer = { fn, at: clock + ms }; timers.push(timer); return timer; };
    const clearTimeout = (timer) => { const at = timers.indexOf(timer); if (at >= 0) timers.splice(at, 1); };
    const advance = async (ms) => {
        const end = clock + ms;
        for (;;) {
            const due = timers.filter((t) => t.at <= end).sort((a, b) => a.at - b.at)[0];
            if (!due) break;
            timers.splice(timers.indexOf(due), 1);
            clock = due.at;
            due.fn();
            await flush();
        }
        clock = end;
    };

    element('[data-role="trace-start-prompt"]').hidden = true;
    element('[data-role="trace-stop-button"]').hidden = true;
    const window = {__resilienceTraceService: traceService, addEventListener(type, fn) {windowListeners[type] = fn;}};
    const sandbox = {window, document, fetch, console, setTimeout, clearTimeout,
        URL, location: {href: 'http://localhost/components/workflows'}, history: {replaceState() {}},
        localStorage: {getItem: () => null, setItem() {}},
        Option: class {constructor(text, value) {this.textContent = text; this.value = value;}},
        Date: class extends Date {static now() {return clock;}}};
    vm.createContext(sandbox);
    vm.runInContext(pollerScript, sandbox);
    vm.runInContext(script, sandbox);
    return {element, field, calls, json, advance, document, listeners, windowListeners};
}
const discoverCalls = env => env.calls.filter(([, url]) => url === '/api/workflows/discovery');
const find = env => env.element('[data-role="guided-auto-discover"]').listeners.click();
const select = env => env.element('[data-role="guided-operation"]');
const status = env => env.element('[data-role="guided-analysis-status"]').textContent;
const workflow = (rootService, operationName, traceCount = 3) => ({rootService, operationName, traceCount});
const report = (state = 'discovered', workflows = [workflow('gateway', 'GET /orders')], extra = {}) =>
    ({state, workflows, message: state, fingerprint: state, truncated: false, ...extra});
const configure = env => {
    env.field('traceEndpoint').value = 'http://localhost:16686';
    env.field('environmentId').value = 'operator-environment';
    env.field('evidenceTier').value = 'experimental';
};

test('startup loads saved workflows without inventing a connection or starting discovery', async () => {
    const env = makeEnvironment();
    await flush();
    assert.equal(env.field('traceEndpoint').value, '');
    assert.equal(discoverCalls(env).length, 0);
    assert.equal(env.calls[0][1], '/api/workflows/experiment-campaigns');
    assert.match(env.element('[data-role="saved-status"]').textContent, /No saved workflows/);
});
test('automatic discovery requires the user connection and does not require an entry service', async () => {
    const env = makeEnvironment({discover: () => env.json(200, report())});
    find(env); await flush(); assert.equal(discoverCalls(env).length, 0);
    configure(env); find(env); await flush();
    const payload = JSON.parse(discoverCalls(env)[0][2]);
    assert.deepEqual(Object.keys(payload).sort(), ['lookback', 'traceSource']);
    assert.equal(payload.traceSource.endpoint, 'http://localhost:16686');
    assert.match(select(env).children[1].textContent, /GET \/orders.*gateway.*3 traces/);
});
test('empty and incomplete evidence retain the backend state and do not invent workflows', async () => {
    for (const state of ['no_traces', 'no_candidates', 'incomplete_traces', 'jaeger_unavailable']) {
        const env = makeEnvironment({discover: () => env.json(200, report(state, []))});
        configure(env); find(env); await flush();
        assert.equal(status(env), state);
        assert.equal(select(env).children.length, 1);
        assert.equal(env.element('[data-role="guided-inspect-button"]').disabled, true);
    }
});
test('no-trace discovery retries and picks up newly arriving evidence', async () => {
    let current = report('no_traces', []);
    const env = makeEnvironment({discover: () => env.json(200, current)});
    configure(env); find(env); await flush();
    current = report();
    await env.advance(9999); assert.equal(discoverCalls(env).length, 1);
    await env.advance(1); assert.equal(discoverCalls(env).length, 2);
    assert.equal(status(env), 'discovered');
});
test('refresh preserves the chosen entry service even when candidate order changes', async () => {
    let current = report('discovered', [workflow('one', 'GET /orders'), workflow('two', 'GET /orders')]);
    const env = makeEnvironment({discover: () => env.json(200, current)});
    configure(env); find(env); await flush(); select(env).value = '1';
    current = report('discovered', [workflow('two', 'GET /orders', 5), workflow('one', 'GET /orders')]);
    await env.advance(30000);
    assert.equal(select(env).value, '0');
    assert.match(select(env).children[1].textContent, /two.*5 traces/);
});
test('hidden pages do not poll and resume when visible', async () => {
    const env = makeEnvironment({hidden: true, discover: () => env.json(200, report())});
    configure(env); find(env); await flush(); assert.equal(discoverCalls(env).length, 0);
    env.document.hidden = false; env.listeners.visibilitychange(); await flush();
    assert.equal(discoverCalls(env).length, 1);
});
test('changing settings cancels a delayed discovery and discards its stale result', async () => {
    let release;
    const pending = new Promise(resolve => release = resolve);
    const env = makeEnvironment({discover: () => pending});
    configure(env); find(env); await flush();
    env.element('[data-role="guided-analysis-form"]').listeners.input({target: {name: 'traceEndpoint'}});
    release(env.json(200, report())); await flush(); await env.advance(120000);
    assert.equal(discoverCalls(env).length, 1);
    assert.match(status(env), /settings changed/);
    assert.equal(env.element('[data-role="guided-discovery"]').hidden, true);
});
test('rejected settings show the server message and stop retries', async () => {
    const env = makeEnvironment({discover: () => env.json(422, {detail: {code: 'REJECTED', message: 'Address is not approved'}})});
    configure(env); find(env); await flush(); await env.advance(120000);
    assert.equal(status(env), 'Address is not approved');
    assert.equal(discoverCalls(env).length, 1);
});
test('bounded discovery tells the user that additional workflows may exist', async () => {
    const env = makeEnvironment({discover: () => env.json(200, report('discovered', [], {truncated: true}))});
    configure(env); find(env); await flush(); assert.match(status(env), /scan reached its limit/);
});

const click = (env, role) => env.element(`[data-role="${role}"]`).listeners.click();
const shown = (env, role) => !env.element(`[data-role="${role}"]`).hidden;
const bridge = (known = 'trace-stopped') => ({available: true, starts: 0, stops: 0, known,
    async status() {return {ok: true, state: this.known, message: this.known};},
    async start() {this.starts++; return {ok: true, state: 'trace-running', message: 'Running'};},
    async stop() {this.stops++; return {ok: true, state: 'trace-stopped', message: 'Stopped'};}});
test('ordinary browsers explain unavailable local tracing without offering container controls', async () => {
    const env = makeEnvironment({discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); find(env); await flush();
    assert.match(env.element('[data-role="find-workflows-status"]').textContent, /Docker Desktop extension/);
    assert.equal(shown(env, 'trace-start-prompt'), false);
    assert.equal(shown(env, 'trace-stop-button'), false);
});
test('extension discovery offers a start without starting anything; declining stays declined across retries', async () => {
    const helper = bridge();
    const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); find(env); await flush();
    assert.equal(shown(env, 'trace-start-prompt'), true); assert.equal(helper.starts, 0);
    await env.advance(10000); assert.equal(shown(env, 'trace-start-prompt'), true);
    click(env, 'trace-start-cancel'); await env.advance(10000);
    assert.equal(shown(env, 'trace-start-prompt'), false); assert.equal(helper.starts, 0);
});
test('only explicit confirmation starts tracing and concurrent clicks do not duplicate it', async () => {
    const helper = bridge();
    let release; helper.start = async () => {helper.starts++; return new Promise(resolve => release = resolve);};
    const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); find(env); await flush();
    const pending = click(env, 'trace-start-confirm'); click(env, 'trace-start-confirm');
    assert.equal(helper.starts, 1);
    helper.known = 'trace-running'; release({ok: true, state: 'trace-running', message: 'Running'}); await pending; await flush();
    assert.equal(shown(env, 'trace-stop-button'), true);
    assert.equal(env.element('[data-role="guided-auto-discover"]').disabled, false);
});
test('the stop control is available only for tracing owned by the extension', async () => {
    for (const state of ['trace-running', 'trace-running-external']) {
        const helper = bridge(state);
        const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report())});
        configure(env); find(env); await flush();
        assert.equal(shown(env, 'trace-stop-button'), state === 'trace-running');
        await click(env, 'trace-stop-button'); await flush();
        assert.equal(helper.stops, state === 'trace-running' ? 1 : 0);
    }
});
test('helper failures remain recoverable and restore the Find control', async () => {
    const helper = bridge(); helper.start = async () => {throw new Error('private docker diagnostic');};
    const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); find(env); await flush(); await click(env, 'trace-start-confirm');
    assert.match(env.element('[data-role="find-workflows-status"]').textContent, /did not complete/);
    assert.equal(env.element('[data-role="guided-auto-discover"]').disabled, false);
    assert.equal(shown(env, 'trace-start-prompt'), false);
});
test('remote trace connections never offer to start a local collector', async () => {
    const helper = bridge();
    const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); env.field('traceEndpoint').value = 'https://tracing.example'; find(env); await flush();
    assert.equal(shown(env, 'trace-start-prompt'), false); assert.equal(helper.starts, 0);
});
test('a different local tracing port cannot accidentally start the fixed extension collector', async () => {
    const helper = bridge();
    const env = makeEnvironment({traceService: helper, discover: () => env.json(200, report('jaeger_unavailable', []))});
    configure(env); env.field('traceEndpoint').value = 'http://localhost:9999'; find(env); await flush();
    assert.equal(shown(env, 'trace-start-prompt'), false); assert.equal(helper.starts, 0);
});
test('recovery closes a start prompt and clears the unavailable message', async () => {
    let current = report('jaeger_unavailable', []);
    const env = makeEnvironment({traceService: bridge(), discover: () => env.json(200, current)});
    configure(env); find(env); await flush(); assert.equal(shown(env, 'trace-start-prompt'), true);
    current = report('no_traces', []); await env.advance(10000);
    assert.equal(shown(env, 'trace-start-prompt'), false);
    assert.equal(env.element('[data-role="find-workflows-status"]').textContent, '');
});
