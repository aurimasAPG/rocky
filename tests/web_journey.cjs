/* State-machine checks use production reducers, without a DOM or model calls. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const root = path.resolve(__dirname, '..');
const context = vm.createContext({ console });
for (const name of ['delivery.js', 'journey.js']) {
  vm.runInContext(fs.readFileSync(path.join(root, 'rocky/static', name), 'utf8'), context);
}
const initial = () => context.initialJourney();
const event = (type, payload = {}) => ({ type, payload, ts: '2026-11-09T12:00:00Z' });
const apply = (state, type, payload = {}) => context.reduceJourney(state, event(type, payload));
const steps = [
  { id: 1, title: 'Research', detail: 'Find evidence' },
  { id: 2, title: 'Draft', detail: 'Save the result' },
];
const planned = () => apply(initial(), 'plan', { steps });
const status = state => state.steps.map(step => `${step.id}:${step.status}`).join(',');
const tests = [];
const test = (name, fn) => tests.push({ name, fn });

test('initial state communicates work without inventing completed steps', () => {
  const state = initial();
  assert.equal(state.stage, 'assessing');
  assert.equal(state.status, 'running');
  assert.equal(state.waiting, false);
  assert.equal(state.steps.length, 0);
});

test('explicit phases work before and after the plan arrives', () => {
  let state = apply(initial(), 'phase', { stage: 'planning' });
  assert.equal(state.stage, 'planning');
  state = apply(state, 'plan', { steps });
  assert.equal(status(state), '1:pending,2:pending');
  state = apply(state, 'phase', { stage: 'working' });
  assert.equal(state.stage, 'working');
  state = apply(state, 'phase', { stage: 'verifying' });
  assert.equal(state.stage, 'verifying');
});

test('concurrent steps are independent even when their titles match', () => {
  let state = apply(initial(), 'plan', { steps: steps.map(step => ({ ...step, title: 'Same title' })) });
  state = apply(state, 'subtask_start', { id: 1, title: 'Same title' });
  state = apply(state, 'subtask_start', { id: 2, title: 'Same title' });
  state = apply(state, 'subtask_done', { id: 2, title: 'Same title', completed: true });
  assert.equal(status(state), '1:active,2:finished');
});

test('incomplete output stays incomplete through the following legacy done event', () => {
  let state = apply(planned(), 'subtask_start', { id: 1 });
  state = apply(state, 'subtask_incomplete', { id: 1, missing: ['deliverables/report.md'] });
  state = apply(state, 'subtask_done', { id: 1, text: 'Worker stopped' });
  assert.equal(status(state), '1:incomplete,2:pending');
});

test('read-only completion is finished while an aborted worker needs attention', () => {
  let state = apply(planned(), 'subtask_start', { id: 1 });
  state = apply(state, 'subtask_done', { id: 1, completed: false, aborted: false });
  assert.equal(state.steps[0].status, 'finished');
  state = apply(state, 'subtask_start', { id: 2 });
  state = apply(state, 'subtask_done', { id: 2, aborted: true });
  assert.equal(state.steps[1].status, 'incomplete');
});

test('saved steps and duplicate completion events do not inflate progress', () => {
  let state = apply(planned(), 'subtask_skipped', { id: 1 });
  state = apply(state, 'subtask_start', { id: 2 });
  state = apply(state, 'subtask_done', { id: 2, completed: true });
  state = apply(state, 'subtask_done', { id: 2, completed: true });
  assert.equal(status(state), '1:skipped,2:finished');
  assert.equal(state.steps.length, 2);
});

test('a new attempt resets the retained plan and records its loop limit', () => {
  let state = apply(planned(), 'loop', { n: 1, max: 3 });
  state = apply(state, 'subtask_done', { id: 1, completed: true });
  state = apply(state, 'loop', { n: 2, max: 3 });
  assert.equal(state.loop, 2);
  assert.equal(state.max, 3);
  assert.equal(status(state), '1:pending,2:pending');
  state = apply(state, 'subtask_skipped', { id: 1 });
  assert.equal(state.steps[0].status, 'skipped');
});

test('a revised plan does not inherit completion from reused step ids', () => {
  let state = apply(planned(), 'subtask_done', { id: 1, completed: true });
  state = apply(state, 'plan', { steps: [{ id: 1, title: 'Fix evidence', detail: 'Resolve the verifier finding' }] });
  assert.equal(status(state), '1:pending');
  assert.equal(state.steps[0].title, 'Fix evidence');
});

test('recoverable errors and failed verification do not end the run', () => {
  let state = apply(planned(), 'error', { message: 'MCP config ignored' });
  assert.equal(state.status, 'running');
  assert(state.notice.includes('MCP config ignored'));
  state = apply(state, 'verify', { passed: false, summary: 'Needs more evidence' });
  assert.equal(state.status, 'running');
  state = apply(state, 'phase', { stage: 'planning' });
  assert.equal(state.stage, 'planning');
});

test('terminal outcomes survive old replay activity, asks and nonterminal snapshots', () => {
  for (const terminal of ['passed', 'passed_with_notes', 'unmet', 'error', 'crashed', 'stopped']) {
    let state = apply(planned(), 'phase', { stage: 'verifying' });
    state = apply(state, 'meta', { status: terminal });
    for (const replay of [event('run_started'), event('phase', { stage: 'working' }), event('ask', { question: 'Old question' }), event('meta', { status: 'running' })]) {
      state = context.reduceJourney(state, replay);
    }
    assert.equal(state.status, terminal);
    if (terminal.startsWith('passed')) assert.equal(state.stage, 'verifying');
    assert.equal(state.waiting, false);
  }
});

test('terminal metadata can refine an outcome and preserves partial work', () => {
  let state = apply(planned(), 'subtask_done', { id: 1, completed: true });
  state = apply(state, 'subtask_incomplete', { id: 2, missing: ['deliverables/result.md'] });
  state = apply(state, 'done', { passed: false });
  state = apply(state, 'meta', { status: 'error' });
  assert.equal(state.status, 'error');
  assert.equal(status(state), '1:finished,2:incomplete');
  state = apply(state, 'meta', { status: 'running' });
  assert.equal(state.status, 'error');
});

test('a stopped history snapshot can reconstruct the stage it actually reached', () => {
  let state = apply(initial(), 'meta', { status: 'stopped' });
  state = apply(state, 'phase', { stage: 'planning' });
  state = apply(state, 'plan', { steps });
  state = apply(state, 'phase', { stage: 'working' });
  state = apply(state, 'subtask_done', { id: 1, completed: true });
  state = apply(state, 'phase', { stage: 'verifying' });
  assert.equal(state.status, 'stopped');
  assert.equal(state.stage, 'verifying');
  assert.equal(state.steps[0].status, 'finished');
});

test('the real clarification tool result clears waiting without needing a terminal event', () => {
  let state = apply(planned(), 'ask', { question: 'Which audience?', ts: 123.5 });
  assert.equal(state.waiting, true);
  state = apply(state, 'tool_result', { name: 'clarify', result: 'Customers' });
  assert.equal(state.waiting, false);
  assert.equal(state.status, 'running');
});

for (const { name, fn } of tests) {
  fn(); console.log(`ok - ${name}`);
}
console.log(`${tests.length} journey state checks passed`);
