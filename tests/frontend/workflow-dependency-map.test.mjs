import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const script = readFileSync(new URL('../../app/static/js/workflow-dependency-map.js', import.meta.url), 'utf8');
const window = {};
vm.runInNewContext(script, {window});
const model = input => JSON.parse(JSON.stringify(window.WorkflowDependencyMap.buildModel(input)));

test('structural mandatory slots and frequency candidates cannot supply validated labels', () => {
    const result = model({observedServices: ['catalog', 'email'], mandatoryServices: ['catalog'],
        assessments: [{serviceName: 'email', classification: 'OPTIONAL_CANDIDATE'}]});
    assert.deepEqual(result.groups.map(group => group.services.length), [0, 0, 0, 2]);
});

test('saved service classifications populate the three separate groups and preserve supporting terminology', () => {
    const input = {assessments: [
        {serviceName: 'payment', classification: 'mandatory', failureEffect: 1},
        {serviceName: 'recommendations', classification: 'supporting', failureEffect: 0.3},
        {serviceName: 'email', classification: 'optional', failureEffect: 0},
    ]};
    const before = JSON.stringify(input);
    const result = model(input);
    assert.deepEqual(result.groups.map(group => group.services.map(service => service.name)), [['payment'], ['recommendations'], ['email'], []]);
    assert.equal(result.groups[1].label, 'Supportive');
    assert.equal(result.groups[1].services[0].assessment.classification, 'supporting');
    assert.equal(JSON.stringify(input), before);
});

test('entry services remain structural and are excluded from classification counts', () => {
    const result = model({entryService: 'web', observedServices: ['web', 'api'], assessments: [{serviceName: 'web', classification: 'mandatory'}]});
    assert.equal(result.services.find(service => service.name === 'web').classification, 'entry');
    assert.equal(result.groups.reduce((count, group) => count + group.services.length, 0), 1);
});

test('missing, conditional and conflicting labels remain unresolved', () => {
    const result = model({observedServices: ['unknown'], assessments: [
        {serviceName: 'conditional', classification: 'conditional'},
        {serviceName: 'conflict', classification: 'mandatory'},
        {serviceName: 'conflict', classification: 'optional'},
    ]});
    assert.deepEqual(result.groups[3].services.map(service => service.name), ['conditional', 'conflict', 'unknown']);
    assert.match(result.services.find(service => service.name === 'conflict').assessment.reasons[0], /Conflicting/);
});

test('trace identities are deduplicated per service and unassessed edge endpoints stay visible', () => {
    const result = model({edges: [
        {source: 'a', target: 'b', evidence: [{traceId: 't1'}, {traceId: 't1'}, {traceId: 't2'}]},
        {source: 'b', target: 'c', evidence: [{traceId: 't2'}]},
    ]});
    assert.deepEqual(result.services.find(service => service.name === 'b').traceIds, ['t1', 't2']);
    assert.deepEqual(result.groups[3].services.map(service => service.name), ['a', 'b', 'c']);
});

test('an empty dataset produces empty groups without invented services', () => {
    const result = model({});
    assert.equal(result.services.length, 0);
    assert.ok(result.groups.every(group => group.services.length === 0));
});
