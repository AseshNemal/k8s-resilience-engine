// Current template + actual selection/dock/controller modules. No Kubernetes I/O.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import { Document, Element, parseInto, flush } from './support/mini-dom.mjs';

Object.defineProperty(Element.prototype, 'dataset', { configurable: true, get() {
    return Object.fromEntries([...this.attrs].filter(([k]) => k.startsWith('data-')).map(([k, v]) => [k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase()), v]));
} });
const read = (path) => readFileSync(new URL(`../../app/${path}`, import.meta.url), 'utf8');
const store = () => { const data = new Map(); return { getItem: k => data.get(k) || null, setItem: (k, v) => data.set(k, v), removeItem: k => data.delete(k) }; };
const candidate = (id, service, over = {}) => ({ id, service, type: 'REPLICA_INCREASE', feasible: true,
    predicted: { blastRadiusBefore: 100, blastRadiusAfter: 50, riskReduction: 50 }, cost: { resource: 'MEDIUM', note: 'Adds one replica and rolls out the Deployment.' },
    approval: { ready: true, requiresJustification: id !== 'first' }, yamlProposal: { fingerprint: `fp-${id}`, sha256: `sha-${id}`, yaml: `kind: Deployment\nmetadata:\n  name: ${service}\n`, applicable: true, status: 'GENERATED' },
    simulatedDeployment: { nodes: [{ name: 'w1' }, { name: 'w2' }], services: [{ name: service, pods: [{ name: `${service}-new`, node: 'w2', ready: true }] }] },
    ranking: { rank: id === 'first' ? 1 : 2 }, ...over });
const candidates = () => [candidate('first', 'details'), candidate('second', 'ratings'), candidate('preview', 'productpage', {
    approval: { ready: false, reason: 'No positive ranked benefit.' }, predicted: { blastRadiusBefore: 100, blastRadiusAfter: 100 }, ranking: { rank: null } })];
const deployment = { nodes: [{ name: 'w1' }, { name: 'w2' }], services: [{ name: 'details', pods: [{ name: 'details-1', node: 'w1', ready: true }] }] };
const approved = (id, over = {}) => ({ recommendationId: id, status: 'APPROVED', evaluationSnapshot: { proposal: { fingerprint: `fp-${id}`, yaml: { sha256: `sha-${id}` } } }, ...over });
function detail() {
    const cs = candidates();
    return { candidates: cs, currentDeployment: deployment, evidence: { failedNode: 'w1' }, yaml: {},
        report: { selectedRecommendationId: 'first', recommendations: cs.map(c => ({ ...c, predictedBlastRadius: c.predicted.blastRadiusAfter })) } };
}
function context(findings = null) {
    return { cluster: { state: 'available', fingerprint: 'cluster-A' }, namespace: 'bookinfo', namespaceChoice: 'only-application',
        namespaces: [{ namespace: 'bookinfo', services: ['details', 'ratings', 'productpage'] }], workflows: findings ? [] : [{ workflowId: 'browse', services: ['details'], applicable: true }],
        evidence: [], ready: !findings, deploymentFindings: findings };
}
function findings() {
    return { scope: 'deployment', currentDeployment: deployment, failedNode: 'w1', nodes: [{ node: 'w1' }, { node: 'w2' }], selectedCandidateId: 'first',
        note: 'Service predictions only; workflow survival unknown.', recommendations: candidates().map(c => ({ ...c, resourceCost: c.cost.resource,
            averageServiceExposureBefore: 50, averageServiceExposureAfter: c.id === 'second' ? 25 : 37.5,
            afterPlacement: [{ service: c.service, node: 'w2', ready: 1 }] })) };
}
async function open({ findings: found = null, storage = store(), approval = () => null, run = null, post = null } = {}) {
    const doc = new Document();
    parseInto(doc, doc.body, read('templates/components/recommendations.html'));
    const calls = [], maps = [], dialogs = [], events = {};
    const window = { document: doc, localStorage: storage, sessionStorage: store(), location: { search: '' }, scrollY: 0,
        addEventListener: (name, fn) => { events[name] = fn; }, ResilienceUi: { formatCodeLabel: x => x, updateLastRefreshed() {} },
        ResiliencePlacement: { fromDeployment: d => d.services, draw: (_, value) => maps.push(value) } };
    const fetch = async (url, init = {}) => {
        calls.push({ url, method: init.method || 'GET', body: init.body && JSON.parse(init.body) });
        let body = {}, status = 200;
        if (url === '/_extension/cluster') body = { context: 'test-cluster' };
        else if (url.startsWith('/api/recommendations/context')) body = context(found);
        else if (url.startsWith('/api/recommendations/live/detail')) body = detail();
        else if (url.endsWith('/approval')) { body = await approval(url.split('/').at(-2)); status = body ? 200 : 404; }
        else if (url.includes('apply-tests/history')) body = { runs: [], hasMore: false };
        else if (url.endsWith('apply-tests/current')) body = { run };
        else if (url.endsWith('applications/current')) body = { application: null };
        else if (init.method === 'POST') body = post ? await post(url, JSON.parse(init.body)) : { ...approved(url.split('/').at(-2)), status: url.includes('request-approval') ? 'PENDING_APPROVAL' : 'APPROVED' };
        else throw new Error(`Unexpected ${url}`);
        return { ok: status < 400, status, json: async () => body, headers: { get: () => null } };
    };
    const timers = (fn, ms) => { const t = setTimeout(fn, ms); t.unref(); return t; };
    const sandbox = { window, document: doc, fetch, URLSearchParams, AbortController, setTimeout: timers, clearTimeout, console };
    vm.createContext(sandbox);
    for (const name of ['api', 'connection-state', 'recommendation-render', 'recommendation-context', 'recommendation-journey', 'recommendation-action', 'recommendation-apply-test'])
        vm.runInContext(read(`static/js/${name}.js`), sandbox, { filename: name });
    window.ResilienceAction.dialog = options => { dialogs.push(options); return Promise.resolve({ confirmed: false }); };
    vm.runInContext(read('static/js/components/recommendations.js'), sandbox);
    for (const fn of doc.listeners.DOMContentLoaded) fn();
    await flush();
    const find = role => doc.querySelector(`[data-role="${role}"]`);
    const select = async id => { const button = doc.querySelector(`[data-select="${id}"]`); assert.ok(button, `${id} is selectable`); button.click(); await flush(); };
    const action = async id => { const b = doc.querySelector(`[data-action="${id}"]`); assert.ok(b, `${id} action exists`); b.dispatchEvent({ type: 'click', preventDefault() {} }); await flush(); };
    return { doc, find, select, action, maps, calls, dialogs, window, events, storage };
}

test('every service-level variant is selectable and updates the dock, prediction and map without a write', async () => {
    const f = findings();
    f.recommendations.push({ ...f.recommendations[1], id: 'ratings-plus-two', type: 'REPLICA_INCREASE_PLUS_2', ranking: { rank: 3 } });
    const t = await open({ findings: f });
    assert.equal(t.find('rec-deployment-findings').querySelectorAll('[data-select]').length, 4);
    await t.select('second');
    assert.match(t.find('rec-dock').textContent, /ratings/);
    assert.match(t.find('rec-dock').textContent, /50% → 25%/);
    assert.equal(t.maps.at(-1).after[0].service, 'ratings');
    assert.equal(t.doc.querySelector('[data-select="second"]').getAttribute('aria-pressed'), 'true');
    assert.match(t.doc.querySelector('[data-action="workflows"]').getAttribute('href'), /purpose=recommendations&namespace=bookinfo/);
    assert.equal(t.doc.querySelector('[data-action="approve"]'), null);
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null);
    assert.ok(t.calls.every(c => c.method === 'GET'));
    t.events.pagehide();
    const restored = await open({ findings: f, storage: t.storage });
    assert.match(restored.find('rec-dock').textContent, /ratings/);
    assert.equal(restored.maps.at(-1).after[0].service, 'ratings');
});

test('workflow candidate switching synchronizes patch and map; a feasible unapprovable option remains inspectable', async () => {
    const t = await open({ approval: id => id === 'first' ? approved(id) : null });
    assert.ok(t.doc.querySelector('[data-action="apply"]'));
    await t.select('second');
    assert.match(t.find('rec-dock').textContent, /ratings/);
    assert.match(t.find('rec-review').textContent, /name: ratings/);
    assert.equal(t.maps.at(-1).after[0].name, 'ratings');
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null, 'first candidate approval is not reused');
    await t.select('preview');
    assert.match(t.find('rec-dock').textContent, /productpage/);
    assert.match(t.find('rec-dock').textContent, /No positive ranked benefit/);
    assert.equal(t.doc.querySelector('[data-action="approve"]'), null);
    assert.ok(t.calls.every(c => c.method === 'GET'));
});

test('an alternative approval asks for justification and a dialog cannot approve after selection changes', async () => {
    const t = await open();
    await t.select('second');
    await t.action('approve');
    const dialog = t.dialogs.at(-1);
    assert.match(dialog.body.textContent, /required; at least 20 characters/);
    assert.match(dialog.body.textContent, /name: ratings/);
    assert.match(await dialog.onConfirm(), /at least 20 characters/);
    dialog.body.querySelector('textarea').value = 'Protect ratings first for this rollout.';
    await t.select('first');
    assert.match(await dialog.onConfirm(), /selection or evidence changed/);
    assert.ok(t.calls.every(c => c.method === 'GET'));
});

test('a saved approval for another proposal never enables Apply; stale pending requests offer rejection', async () => {
    const t = await open({ approval: id => approved(id, { evaluationSnapshot: { proposal: { fingerprint: 'old', yaml: { sha256: 'old' } } } }) });
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null);
    assert.match(t.find('approval-state-container').textContent, /Earlier proposal only/);
    const pending = await open({ approval: id => ({ ...approved(id), status: 'PENDING_APPROVAL', evaluationSnapshot: {} }) });
    assert.ok(pending.doc.querySelector('[data-action="reject"]'));
    assert.equal(pending.doc.querySelector('[data-action="approve"]'), null);
});

test('late approval reads from the first visit cannot replace an A → B → A selection', async () => {
    let release, reads = 0;
    const t = await open({ approval: id => id === 'first' && ++reads === 1 ? new Promise(resolve => { release = resolve; }) : null });
    await t.select('second');
    await t.select('first');
    release(approved('first'));
    await flush();
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null);
    assert.ok(t.doc.querySelector('[data-action="approve"]'));
});

test('an in-progress run blocks actions for a newly selected candidate; a settled kept run allows its review', async () => {
    const t = await open({ run: { runId: 'run-1', candidateId: 'first', status: 'RUNNING', events: [] } });
    await t.select('second');
    assert.equal(t.find('rec-dock').getAttribute('data-phase'), 'running');
    assert.equal(t.doc.querySelector('[data-action="approve"]'), null);
    const kept = await open({ run: { runId: 'run-2', candidateId: 'first', status: 'COMPLETED', application: { status: 'KEPT' }, kept: { by: 'operator' }, events: [] } });
    await kept.select('second');
    assert.ok(kept.doc.querySelector('[data-action="approve"]'));
    assert.ok(kept.calls.every(c => c.method === 'GET'));
});

test('approval binds the chosen alternative and Apply requires its own confirmation with that proposal identity', async () => {
    const t = await open();
    t.window.ResilienceAction.attribution.set('Test Operator');
    await t.select('second');
    await t.action('approve');
    const approveDialog = t.dialogs.at(-1);
    approveDialog.body.querySelector('textarea').value = 'Protect ratings first with this separate proposal.';
    assert.equal(await approveDialog.onConfirm(), null);
    const posts = t.calls.filter(c => c.method === 'POST');
    assert.equal(posts.length, 2);
    assert.match(posts[0].url, /\/second\/request-approval\?/);
    assert.match(posts[1].url, /\/second\/approve$/);
    assert.ok(!posts.some(c => c.url.endsWith('/apply-tests')));
    await t.action('apply');
    const applyDialog = t.dialogs.at(-1);
    assert.match(applyDialog.body.textContent, /name: ratings/);
    assert.equal(t.calls.filter(c => c.method === 'POST').length, 2, 'opening the Apply dialog is read-only');
    assert.equal(await applyDialog.onConfirm(), null);
    await flush();
    const applied = t.calls.find(c => c.url === '/api/recommendations/apply-tests' && c.method === 'POST');
    assert.equal(applied.body.candidateId, 'second');
    assert.equal(applied.body.fingerprint, 'fp-second');
    assert.equal(applied.body.yamlSha256, 'sha-second');
});

test('an approval lookup that cannot be read never offers approval or Apply', async () => {
    const t = await open({ approval: () => { throw new Error('network unavailable'); } });
    assert.match(t.find('rec-dock').textContent, /Could not load the approval state/);
    assert.ok(t.doc.querySelector('[data-action="recheck"]'));
    assert.equal(t.doc.querySelector('[data-action="approve"]'), null);
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null);
});

test('missing workflow evidence does not hide an existing change that needs Keep or Reset', async () => {
    const t = await open({ findings: findings(), run: { runId: 'existing', candidateId: 'old', status: 'COMPLETED', canReset: true, canKeep: true, events: [] } });
    assert.ok(t.doc.querySelector('[data-action="reset"]'));
    assert.ok(t.doc.querySelector('[data-action="keep"]'));
    assert.equal(t.doc.querySelector('[data-action="approve"]'), null);
});

test('a proposal changed during the approval request is never automatically approved', async () => {
    const t = await open({ post: () => approved('first', { status: 'PENDING_APPROVAL', evaluationSnapshot: { proposal: { fingerprint: 'changed', yaml: { sha256: 'changed' } } } }) });
    await t.action('approve');
    assert.match(await t.dialogs.at(-1).onConfirm(), /different proposal/);
    assert.equal(t.calls.filter(c => c.method === 'POST').length, 1, 'the unseen proposal is requested but never approved');
    assert.ok(t.doc.querySelector('[data-action="reject"]'));
    assert.equal(t.doc.querySelector('[data-action="apply"]'), null);
});

test('switching candidates invalidates a previously opened Apply confirmation', async () => {
    const t = await open({ approval: id => approved(id) });
    await t.action('apply');
    const dialog = t.dialogs.at(-1);
    await t.select('second');
    assert.match(await dialog.onConfirm(), /selection or evidence changed/);
    assert.ok(t.calls.every(c => c.method === 'GET'));
});

test('unknown feasibility is listed as missing evidence and never becomes a selectable alternative', async () => {
    const f = findings();
    f.recommendations.push({ id: 'unknown', service: 'unresolved', type: 'TOPOLOGY_SPREAD', feasibility: { status: 'INSUFFICIENT_EVIDENCE' } });
    const t = await open({ findings: f });
    assert.equal(t.doc.querySelector('[data-select="unknown"]'), null);
    assert.match(t.find('rec-deployment-findings').textContent, /INSUFFICIENT_EVIDENCE/);
});
