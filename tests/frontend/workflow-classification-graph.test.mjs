import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const source = readFileSync(new URL('../../app/static/js/components/workflows.js', import.meta.url), 'utf8');
class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.attributes = {}; this.dataset = {}; this.style = {}; this.listeners = {}; this.isConnected = true; this.classList = {add() {}, toggle() {}}; }
    setAttribute(name, value) { this.attributes[name] = value; }
    append(...children) { this.children.push(...children); }
    appendChild(child) { this.append(child); }
    addEventListener(name, callback) { this.listeners[name] = callback; }
    querySelectorAll(selector) {
        return this.children.flatMap(child => {
            if (!(child instanceof Element)) return [];
            return [...(child.attributes.class === selector.slice(1) ? [child] : []), ...child.querySelectorAll(selector)];
        });
    }
}
function workspace(options = {}) {
    const context = {window: {}, ...options, document: {
        querySelector: () => null,
        createElement: tag => new Element(tag),
        createElementNS: (_, tag) => new Element(tag),
    }};
    vm.createContext(context);
    vm.runInContext(source.replace('    if (!guidedForm) return;',
        'globalThis.project = assessmentGraph; globalThis.draw = renderGraph; globalThis.groups = dependencyGroups; globalThis.summary = executionSummary; globalThis.runHistory = renderRunHistory; return;'), context);
    return context;
}
const fixture = () => ({
    structuralEntryService: 'gateway',
    candidateGraph: {
        // These are structural placeholder slots, not experimental labels.
        mandatoryServices: ['gateway', 'payment', 'email', 'recommendations', 'catalog'],
        edges: ['payment', 'email', 'recommendations', 'catalog'].map(target => ({source: 'gateway', target})),
    },
    services: [
        {serviceName: 'payment', classification: 'mandatory'},
        {serviceName: 'email', classification: 'optional'},
        {serviceName: 'recommendations', classification: 'supporting'},
        {serviceName: 'catalog', classification: 'insufficient_evidence'},
    ],
    edges: [],
});
const plain = value => JSON.parse(JSON.stringify(value));

test('projects measured service labels rather than structural mandatory placeholders', () => {
    const env = workspace();
    const assessment = fixture();
    assessment.services.push({serviceName: 'conditional', classification: 'conditional'});
    const graph = plain(env.project(assessment));
    assert.deepEqual(graph.mandatoryServices, ['payment']);
    assert.deepEqual(graph.optionalServices, ['email']);
    assert.deepEqual(graph.supportingServices, ['recommendations']);
    assert.deepEqual(graph.unresolvedServices, ['catalog', 'conditional']);
    assert.equal(graph.entryService, 'gateway');
    assert.equal(assessment.candidateGraph.mandatoryServices.length, 5);
});

test('renders supporting and unresolved nodes and treats the entry as structural', () => {
    const env = workspace(), container = new Element('div');
    env.draw(container, env.project(fixture()));
    const badges = Object.fromEntries(container.querySelectorAll('.dag-node').map(node => [node.dataset.service, node.children.at(-1).textContent]));
    assert.deepEqual(badges, {gateway: 'ENTRY', payment: 'MANDATORY', email: 'OPTIONAL', recommendations: 'SUPPORTING', catalog: 'UNRESOLVED'});
    const edges = container.querySelectorAll('.dag-edge-path');
    assert.ok(edges.every(edge => edge.dataset.mandatory === 'false'));
    assert.ok(edges.every(edge => edge.children[0].textContent.endsWith('edge necessity unresolved')));
});

test('an edge receives a necessity label only from its own assessment', () => {
    const env = workspace(), container = new Element('div'), assessment = fixture();
    assessment.edges = [
        {sourceService: 'gateway', targetService: 'payment', classification: 'mandatory'},
        {sourceService: 'gateway', targetService: 'email', classification: 'optional'},
        {sourceService: 'gateway', targetService: 'recommendations', classification: 'supporting'},
    ];
    env.draw(container, env.project(assessment));
    const edges = container.querySelectorAll('.dag-edge-path');
    assert.deepEqual(edges.map(edge => edge.dataset.mandatory), ['true', 'false', 'false', 'false']);
    assert.match(edges[1].children[0].textContent, /optional edge$/);
    assert.match(edges[2].children[0].textContent, /supporting edge$/);
    const email = container.querySelectorAll('.dag-node').find(node => node.dataset.service === 'email');
    email.listeners.mouseenter();
    email.listeners.mouseleave();
    assert.equal(edges[1].attributes.stroke, '#279f86');
    assert.equal(edges[2].attributes.stroke, '#3999d8');
});

test('a supporting service remains visible even when absent from downstream graph arrays', () => {
    const env = workspace(), container = new Element('div');
    env.draw(container, {mandatoryServices: ['payment'], optionalServices: [], supportingServices: ['recommendations'], edges: []});
    assert.deepEqual(container.querySelectorAll('.dag-node').map(node => node.dataset.service), ['payment', 'recommendations']);
    assert.equal(container.querySelectorAll('.dag-node')[0].children.at(-1).textContent, 'MANDATORY');
    assert.equal(container.querySelectorAll('.dag-node')[1].children.at(-1).textContent, 'SUPPORTING');
});

test('the presentation groups optional and supporting without losing their distinct labels', () => {
    const env = workspace();
    const groups = env.groups(env.project(fixture()));
    const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');
    assert.match(text(groups), /Mandatory/);
    assert.match(text(groups), /Optional \/ Supporting/);
    assert.match(text(groups), /email \(optional\)/);
    assert.match(text(groups), /recommendations \(supporting\)/);
    assert.match(text(groups), /Unresolved/);
    assert.match(text(groups), /catalog/);
});

test('a graph with no assessed labels invents neither mandatory dependencies nor an entry', () => {
    const env = workspace(), container = new Element('div');
    env.draw(container, {observedServices: ['a', 'b'], edges: [{source: 'a', target: 'b'}]});
    assert.deepEqual(container.querySelectorAll('.dag-node').map(node => node.children.at(-1).textContent), ['NOT TESTED', 'NOT TESTED']);
    assert.equal(container.querySelectorAll('.dag-edge-path')[0].dataset.mandatory, 'false');
});

test('the actual request and response assertion are shown independently of the workflow name', () => {
    const env = workspace();
    const text = env.summary({workflowName: 'GET /checkout', executionDefinition: {steps: [
        {method: 'GET', path: '/', responseContains: ['success']},
    ]}});
    assert.match(text, /Actual requests: GET \/ · required response text: "success"/);
    assert.doesNotMatch(text, /checkout/);
});

test('failed-run history opens and loads reasons immediately without a toggle click', async () => {
    const calls = [];
    const env = workspace({fetch: async url => {
        calls.push(url);
        return {ok: true, json: async () => [{recordedAt: '2026-10-25T09:08:15Z', status: 'failed', correlationStatus: 'matched',
            reasons: ['response did not contain required marker(s): success'], stepEvidence: []}]};
    }});
    const panel = env.runHistory('baseline:failed', 'Normal run results', true);
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(panel.open, true);
    assert.equal(calls.length, 1);
    const text = node => [node.textContent || '', ...node.children.map(text)].join(' ');
    assert.match(text(panel), /response did not contain required marker/);
    await panel.listeners.toggle();
    assert.equal(calls.length, 1);
});
