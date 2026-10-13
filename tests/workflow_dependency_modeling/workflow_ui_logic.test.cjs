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
vm.runInContext(source.replace(/\}\)\(\);\s*$/, 'globalThis.buildSetup = buildObservedWorkflowSetup; })();'), context);
const build = (...args) => JSON.parse(JSON.stringify(context.buildSetup(...args)));

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
