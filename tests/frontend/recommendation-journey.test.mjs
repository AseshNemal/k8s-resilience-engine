import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { Document } from './support/mini-dom.mjs';
const context = { window: {} };
vm.runInNewContext(readFileSync(new URL('../../app/static/js/recommendation-journey.js', import.meta.url), 'utf8'), context);
const journey = context.window.ResilienceJourney;
test('worst case across nodes remains distinct from the assessed node prediction', () => {
    const n = journey.numbers({ predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0 }, coverage: { worstCaseBefore: 100, worstCaseAfter: 21.82 } });
    assert.equal(n.after, 21.82); assert.equal(n.node.after, 0); assert.equal(n.scope, 'worst');
});
test('a missing exposure entry does not claim the workflow survives after a change', () => {
    const model = journey.exposure({ workflowExposure: { before: { a: ['w1'], b: ['w2'] }, after: { fix: { a: [] } } } }, { id: 'fix' });
    assert.equal(model.hasAfter, false); assert.equal(model.safeAfter, null);
});
test('the selected combined fix is drawn once and selection invokes the page callback', () => {
    const doc = new Document(), root = doc.createElement('div'); const selected = [];
    const candidate = (id) => ({ id, type: 'COMBINATION', service: 'a+b', feasible: true, predicted: { blastRadiusBefore: 100, blastRadiusAfter: 0 }, parts: [{ type: 'REPLICA_INCREASE' }] });
    journey.renderFixes(root, { candidates: [candidate('one'), candidate('two')] }, { document: doc, selectedId: 'one', onSelect: id => selected.push(id) });
    assert.equal(root.querySelectorAll('[data-candidate="one"]').length, 1);
    root.querySelector('[data-select="two"]').click(); assert.equal(selected[0], 'two');
    assert.ok(root.textContent.includes('Add a replica to each'));
});
test('approval alone never claims the proposal was applied or tested', () => {
    const model = journey.stages({ status: 'ready', selected: true, approval: 'APPROVED' });
    assert.equal(model.stages.find(s => s.id === 'apply').note, 'Ready to apply');
    assert.equal(model.stages.find(s => s.id === 'decide').state, 'waiting');
});
test('a completed run offers a keep or reset decision only when the server permits it', () => {
    const model = journey.stages({ status: 'ready', selected: true, approval: 'APPROVED', run: { finished: true, canDecide: true } });
    assert.equal(model.next.label, 'Keep or reset the change');
});
