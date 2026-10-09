import assert from 'node:assert/strict';
import test from 'node:test';
import { taskOrigin, agentOrigin, conversationSubject } from '../src/message-origin.ts';

const origin = { kind: 'task', trigger: 'time', task_id: 'timer', title: 'Water the plants', occurrence: 2 };

test('only explicit task provenance changes the input presentation, never the reminder reply', () => {
  const wake = { role: 'user', content: 'Internal timer dispatch text', origin };
  assert.deepEqual(taskOrigin(wake), origin);
  assert.equal(wake.content, 'Internal timer dispatch text');
  assert.equal(taskOrigin({ role: 'assistant', origin, content: 'Time to water the plants.' }), undefined);
  assert.equal(taskOrigin({ role: 'user', content: '[已登记任务到期：Water the plants；任务 ID timer，第 2 次。]' }), undefined);
});

test('delegation receipts require durable metadata and never relabel an assistant reply', () => {
  const source = { kind: 'agent_results', wait_id: 'wait', agents: [{ id: 'job', title: 'Research', status: 'complete', session_id: 'child', run_id: 'run' }] };
  assert.deepEqual(agentOrigin({ role: 'user', origin: source }), source);
  assert.equal(agentOrigin({ role: 'assistant', origin: source }), undefined);
  assert.equal(agentOrigin({ role: 'user', content: '[Delegated task results]' }), undefined);
  for (const invalid of [{ ...source, agents: [] }, { ...source, agents: [null] }, { ...source, agents: [{ ...source.agents[0], run_id: null }] }, { kind: 'agent_task', id: 'job' }]) {
    assert.equal(agentOrigin({ role: 'user', origin: invalid }), undefined);
  }
});

test('old clients and malformed or unknown origins retain the ordinary message', () => {
  for (const value of [undefined, null, [], 'task', { ...origin, kind: 'future-kind' }, { ...origin, title: null },
                       { ...origin, trigger: 'unknown' }, { ...origin, occurrence: -1 }, { ...origin, occurrence: '2' }]) {
    assert.equal(taskOrigin({ role: 'user', origin: value }), undefined);
  }
  assert.equal(taskOrigin({ role: 'user', origin: { ...origin, trigger: 'condition' } }).trigger, 'condition');
});

test('companion labels use the registered reminder title while preserving the complete trigger', () => {
  const content = '[已登记任务到期：Water the plants；任务 ID timer，第 2 次。请按之前登记的要求执行。]';
  const messages = [{ role: 'user', content: 'Remind me to water the plants in an hour.' },
    { role: 'user', content, origin }, { role: 'assistant', content: 'Time to water the plants.' }];
  const original = JSON.stringify(messages);
  assert.equal(conversationSubject(messages), 'Water the plants');
  assert.equal(JSON.stringify(messages), original);
  assert.equal(conversationSubject([{ role: 'user', content }]), content, 'ordinary user text is never classified by its wording');
});

test('delegated-result instructions do not replace the user request in companion labels', () => {
  const messages = [{ role: 'user', content: 'Compare the two quotes.' }, { role: 'user', content: 'Internal result collection instructions',
    origin: { kind: 'agent_results', wait_id: 'wait', agents: [{ id: 'job', title: 'Compare prices', status: 'complete', session_id: 'child', run_id: 'run' }] } }];
  assert.equal(conversationSubject(messages), 'Compare the two quotes.');
  assert.equal(conversationSubject([messages[1]]), undefined);
  assert.equal(conversationSubject([{ role: 'user', content: 'Internal delegated instructions',
    origin: { kind: 'agent_task', id: 'job', title: 'Compare prices', parent_session_id: 'parent' } }]), 'Compare prices');
  assert.equal(conversationSubject([...messages, { role: 'user', content: 'Actually, compare all three.' }]), 'Actually, compare all three.');
});
