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
vm.runInContext(source.replace('    if (!guidedForm) return;', 'globalThis.describeOperation = describeOperation; globalThis.buildSetup = buildObservedWorkflowSetup; globalThis.selectWorkflow = selectedGuidedWorkflow; globalThis.observedGraphModel = observedGraphModel; globalThis.stageLabel = stageLabel; return;'), context);
const build = (...args) => JSON.parse(JSON.stringify(context.buildSetup(...args)));

test('workflow stage labels distinguish every persisted lifecycle state', () => {
    assert.equal(context.stageLabel('baseline_required'), 'Normal runs needed');
    assert.equal(context.stageLabel('interventions_required'), 'Service tests needed');
    assert.equal(context.stageLabel('ready_for_assessment'), 'Ready to review');
    assert.equal(context.stageLabel('reviewed'), 'Awaiting approval');
    assert.equal(context.stageLabel('approved'), 'Approved');
    assert.equal(context.stageLabel('unknown'), 'Checking status');
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


test('operation guidance distinguishes HTTP replay from internal operations', () => {
    for (const operation of ['GET /items/{id}', 'post /orders', 'PATCH /profile']) {
        const description = context.describeOperation(operation);
        assert.equal(description.direct, true);
        assert.match(description.text, /does not copy its request body, cookies or earlier steps/);
    }
    assert.match(context.describeOperation('POST /orders').text, /may change application data/);
    assert.doesNotMatch(context.describeOperation('GET /items').text, /may change application data/);
    for (const operation of ['PlaceOrder', '/shop.Checkout/PlaceOrder', 'GET', 'HEAD /items']) {
        const description = context.describeOperation(operation);
        assert.equal(description.direct, false);
        assert.match(description.text, /provide an HTTP execution definition/);
    }
});
