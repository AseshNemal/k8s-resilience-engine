import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../../app/static/js/components/workflows.js', import.meta.url), 'utf8');
// Exercise the rendering functions with API and DOM doubles; no execute route is called.
const functions = source.slice(source.indexOf('    function scoreRow('), source.indexOf('    async function loadCampaignDecision('));
function environment() {
    const node = (tag, className = '', textContent = '') => ({tag, className, textContent, children: [],
        dataset: {}, isConnected: true, listeners: {}, value: '',
        append(...items) { this.children.push(...items); },
        replaceChildren(...items) { this.children = items; },
        addEventListener(name, callback) { this.listeners[name] = callback; }});
    const field = (label, type, value = '') => {
        const wrapper = node('div', '', label), input = node('input');
        input.value = value;
        wrapper.append(input);
        return {label: wrapper, input};
    };
    const posts = [];
    const context = vm.createContext({
        document: {createElement: tag => node(tag)}, guidedText: node, field,
        selectField(label, choices) {
            const result = field(label);
            result.input.tag = 'select';
            result.input.choices = choices;
            return result;
        },
        details: title => node('details', '', title),
        button(label, callback) { const result = node('button', '', label); result.click = callback; return result; },
        campaignUrl: () => '/campaign', requestScenario: async () => [],
        postCampaignAction: async (url, payload) => posts.push({url, payload}),
        message: () => {},
    });
    const api = vm.runInContext(`${functions}\n({heldOutPanel, heldOutRun})`, context);
    const descendants = root => [root, ...root.children.flatMap(descendants)];
    return {...api, posts, descendants};
}

test('held-out controls default to stratified and send the selected sampling method', async () => {
    const env = environment();
    const panel = env.heldOutPanel({});
    panel.open = true;
    panel.listeners.toggle();
    await new Promise(resolve => setImmediate(resolve));
    let nodes = env.descendants(panel);
    const sampling = nodes.find(item => item.textContent === 'Sampling method').children[0];
    const count = nodes.find(item => item.textContent === 'Pairs to test');
    assert.equal(sampling.value, 'stratified');
    assert.deepEqual(Array.from(sampling.choices, choice => choice[0]), ['stratified', 'uniform']);
    assert.ok(nodes.some(item => item.children.includes(count) && item.children.some(child => child.children.includes(sampling))));
    await nodes.find(item => item.textContent === 'Freeze predictions for a new run').click();
    assert.equal(env.posts[0].payload.sampling, 'stratified');
    assert.equal(env.posts[0].payload.scenarioCount, 10);
    await new Promise(resolve => setImmediate(resolve));
    nodes = env.descendants(panel);
    nodes.find(item => item.textContent === 'Sampling method').children[0].value = 'uniform';
    nodes.find(item => item.textContent === 'Random seed (optional)').children[0].value = '7';
    await nodes.find(item => item.textContent === 'Freeze predictions for a new run').click();
    assert.equal(env.posts[1].payload.sampling, 'uniform');
    assert.equal(env.posts[1].payload.seed, 7);
});

test('held-out run header shows strata and caveat, and accepts legacy runs', () => {
    const env = environment();
    const metrics = {scored: 0, truePositives: 0, falsePositives: 0, falseNegatives: 0, trueNegatives: 0};
    const run = {runId: 'run', counts: {observed: 0, planned: 0}, method: metrics,
        methodUncertainCountedWrong: metrics, traceOnly: metrics, scenarios: [],
        samplingMethod: 'stratified', samplingGroups: {fail: {available: 7, drawn: 3}, survive: {available: 1, drawn: 1}},
        samplingNote: 'Accuracy describes these strata, not all pairs.'};
    const texts = env.descendants(env.heldOutRun({}, run, () => {})).map(item => item.textContent);
    assert.ok(texts.includes('stratified: fail: 3/7 pairs drawn · survive: 1/1 pairs drawn'));
    assert.ok(texts.includes(run.samplingNote));
    delete run.samplingGroups;
    delete run.samplingMethod;
    delete run.samplingNote;
    assert.doesNotThrow(() => env.heldOutRun({}, run, () => {}));
});

test('an opened scenario list stays open when a running job redraws the run', async () => {
    const env = environment();
    const metrics = {scored: 0, truePositives: 0, falsePositives: 0, falseNegatives: 0, trueNegatives: 0};
    const run = {runId: 'run', counts: {observed: 0, planned: 0}, method: metrics,
        methodUncertainCountedWrong: metrics, traceOnly: metrics, scenarios: []};
    const opened = new Set();
    const list = box => env.descendants(box).find(item => item.tag === 'details');
    const first = list(env.heldOutRun({}, run, () => {}, opened));
    assert.equal(first.open, false);
    first.open = true;
    first.listeners.toggle();
    assert.equal(list(env.heldOutRun({}, run, () => {}, opened)).open, true);
    const closed = list(env.heldOutRun({}, run, () => {}, opened));
    closed.open = false;
    closed.listeners.toggle();
    assert.equal(list(env.heldOutRun({}, run, () => {}, opened)).open, false);
});

test('the sampling inputs are kept when the runs below are redrawn', async () => {
    const env = environment();
    const panel = env.heldOutPanel({}, true);
    await new Promise(resolve => setImmediate(resolve));
    const seed = env.descendants(panel).find(item => item.textContent === 'Random seed (optional)').children[0];
    seed.value = '42';
    await env.descendants(panel).find(item => item.textContent === 'Freeze predictions for a new run').click();
    await new Promise(resolve => setImmediate(resolve));
    const after = env.descendants(panel).find(item => item.textContent === 'Random seed (optional)').children[0];
    assert.equal(after, seed);
    assert.equal(after.value, '42');
});
