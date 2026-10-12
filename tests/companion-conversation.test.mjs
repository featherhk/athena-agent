import assert from 'node:assert/strict';
import test from 'node:test';
import { companionConversationStart } from '../src/companion-conversation.ts';

const messages = [
  { id: 'u1', role: 'user', run_id: 'one', content: 'Compare these quotations.' },
  { id: 'a1', role: 'assistant', run_id: 'one', content: 'The comparison is ready.' },
  { id: 'u2', role: 'user', run_id: 'two', content: 'Add installation costs.' },
  { id: 'a2', role: 'assistant', run_id: 'two', content: 'Updating the comparison.' },
];

test('the latest exchange includes its question and leaves durable history untouched', () => {
  const before = structuredClone(messages);
  assert.equal(companionConversationStart(messages, [{ id: 'one', status: 'complete' }, { id: 'two', status: 'complete' }]), 2);
  assert.deepEqual(messages, before);
  assert.equal(companionConversationStart([], []), 0);
  assert.equal(companionConversationStart([messages[1]], []), 0);
  assert.equal(companionConversationStart([...messages, { id: 'local', role: 'user', content: 'Next' }], []), 4);
});

test('queueing a new request cannot hide an earlier unfinished request or user help', () => {
  for (const status of ['queued', 'running', 'waiting_user', 'waiting_agents']) {
    assert.equal(companionConversationStart(messages, [{ id: 'one', status }, { id: 'two', status: 'queued' }]), 0, status);
  }
  const helpReply = { id: 'help', role: 'user', run_id: 'two', content: 'Include materials', help_reply: { request_id: 'help' } };
  assert.equal(companionConversationStart([...messages, helpReply], [{ id: 'two', status: 'complete' }]), 2);
});

test('internal agent results do not create a new human conversation boundary', () => {
  const internal = { id: 'agent', role: 'user', run_id: 'internal', content: 'Result envelope', origin: { kind: 'agent_results' } };
  assert.equal(companionConversationStart([...messages, internal], []), 2);
});
