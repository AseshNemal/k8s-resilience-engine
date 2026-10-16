// Browser-independent unit tests for the actual guided-form payload builder.
const {readFileSync} = require('node:fs');
const {resolve} = require('node:path');
const vm = require('node:vm');
const {test} = require('node:test');
const assert = require('node:assert/strict');
const source = readFileSync(resolve(__dirname, '../../app/static/js/components/workflows.js'), 'utf8');
const context = {
    window: {ResilienceApi: {}, ResilienceUi: {}},
    document: {querySelector: () => null, querySelectorAll: () => [], addEventListener: () => {}},
};
vm.createContext(context);
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.buildSetup = buildObservedWorkflowSetup; globalThis.selectWorkflow = selectedGuidedWorkflow; globalThis.observedGraphModel = observedGraphModel; globalThis.isLocalBoutiqueDemo = isLocalBoutiqueDemo; })();'), context);
const build = (...args) => JSON.parse(JSON.stringify(context.buildSetup(...args)));

test('local Docker action is offered only to the reviewed local boutique profile', () => {
    const campaign = {workloadProfile: 'online_boutique_checkout_v0_10_1'};
    assert.equal(context.isLocalBoutiqueDemo(campaign, 'http://localhost:8080'), true);
    assert.equal(context.isLocalBoutiqueDemo(campaign, 'https://production.example'), false);
    assert.equal(context.isLocalBoutiqueDemo({workloadProfile: 'reviewed_generic'}, 'http://localhost:8080'), false);
});

test('approved output distinguishes trace-observed edges from outage-validated services', () => {
    assert.match(source, /Observed call edges/);
    assert.match(source, /not individually outage-validated/);
    assert.match(source, /badge\.textContent = 'Step 5: graph approved'/);
    assert.match(source, /Download the approved graph JSON or inspect it through the workflow API/);
    assert.match(source, /progress\.stage === "approved"/);
});

test('keeps response markers as a list and preserves generic request data', () => {
    const setup = build('200, 201, 200', '{"pathVariables":{"id":"42"},"jsonBody":{"quantity":2},"responseContains":["order"]}', ' confirmed ');
    assert.deepEqual(setup.expectedStatusCodes, [200, 201]);
    assert.deepEqual(setup.request.responseContains, ['order', 'confirmed']);
    assert.deepEqual(setup.request.pathVariables, {id: '42'});
    assert.deepEqual(setup.request.jsonBody, {quantity: 2});
});

test('rejects empty, fractional, out-of-range and partially valid status inputs', () => {
    for (const value of ['', '200,', '0', '600', '200,abc', '200.5']) {
        assert.throws(() => build(value, '{}', ''), /HTTP status/);
    }
});

test('rejects non-object options and string responseContains', () => {
    for (const value of ['null', '[]', '42', '"text"']) {
        assert.throws(() => build('200', value, ''), /JSON object/);
    }
    assert.throws(() => build('200', '{"responseContains":"ok"}', ''), /list/);
});

test('does not invent success markers or request data', () => {
    assert.deepEqual(build('204', '', ''), {mode: 'observed_entry', expectedStatusCodes: [204], request: {}});
});

test('workflow selection preserves distinct services with the same operation name', () => {
    const workflows = [
        {rootService: 'gateway-a', operationName: 'POST /payment'},
        {rootService: 'gateway-b', operationName: 'POST /payment'},
    ];
    assert.equal(context.selectWorkflow(workflows, '0'), workflows[0]);
    assert.equal(context.selectWorkflow(workflows, '1'), workflows[1]);
    for (const selection of ['', 'POST /payment', '-1', '99']) {
        assert.equal(context.selectWorkflow(workflows, selection), undefined);
    }
});

test('observed graph uses recorded edges without inventing mandatory dependencies', () => {
    const graph = JSON.parse(JSON.stringify(context.observedGraphModel({
        observedServices: ['frontend', 'checkoutservice', 'paymentservice'],
        edges: [{source: 'frontend', target: 'checkoutservice'}, {source: 'checkoutservice', target: 'paymentservice'}],
    })));
    assert.deepEqual(graph, {
        observedServices: ['frontend', 'checkoutservice', 'paymentservice'],
        edges: [{source: 'frontend', target: 'checkoutservice'}, {source: 'checkoutservice', target: 'paymentservice'}],
        mandatoryServices: [],
        optionalServices: [],
    });
});
