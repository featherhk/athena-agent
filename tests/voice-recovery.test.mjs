import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceRecoveryStore, VoiceSubmissionError, VoiceSubmissionReceipt } from '../src/voice-recovery.ts';

test('only pre-dispatch failures are definitely unsent; a missing receipt remains uncertain', () => {
  const receipt = new VoiceSubmissionReceipt();
  assert.equal(receipt.failure(new Error('observation failed')).delivery, 'not_sent');
  receipt.dispatch();
  assert.equal(receipt.failure(new TypeError('lost response')).delivery, 'unknown');
  assert.equal(receipt.failure(new Error('malformed response')).delivery, 'unknown');
});

test('an accepted POST is not reclassified as unsent by a subsequent refresh failure', () => {
  const receipt = new VoiceSubmissionReceipt();
  receipt.dispatch(); receipt.confirm();
  assert.equal(receipt.failure(new Error('refresh failed')), undefined);
});

test('recognized words are scoped to the owner and session, and readers cannot mutate retained records', () => {
  const store = new VoiceRecoveryStore();
  store.selectOwner('owner1');
  const scope = store.scope('owner1', 'chat1');
  assert.equal(store.retain(scope, 'Please book a train', new VoiceSubmissionError('not_sent')), true);
  assert.equal(store.read('owner1', 'chat1').text, 'Please book a train');
  assert.equal(store.read('owner1', 'chat1').delivery, 'not_sent');
  assert.equal(store.read('owner1', 'chat2'), null);
  assert.equal(store.read('owner2', 'chat1'), null);
  const copy = store.read('owner1', 'chat1'); copy.text = 'External mutation';
  assert.equal(store.read('owner1', 'chat1').text, 'Please book a train');
});

test('navigation retains late failures in the original conversation without replaying submission', async () => {
  const store = new VoiceRecoveryStore(); store.selectOwner('owner');
  const scope = store.scope('owner', 'original');
  let attempts = 0;
  const post = async () => { attempts++; throw new Error('response lost after acceptance'); };
  try { await post(); } catch (error) { store.retain(scope, 'Make one reservation', error); }
  assert.equal(store.read('owner', 'other'), null);
  assert.equal(store.read('owner', 'original').delivery, 'unknown');
  assert.equal(store.read('owner', 'original').text, 'Make one reservation');
  assert.equal(attempts, 1);
});

test('logout clears words and rejects late failures even after the same owner logs in again', () => {
  const store = new VoiceRecoveryStore(); store.selectOwner('owner');
  const scope = store.scope('owner', 'chat');
  store.retain(scope, 'Private words', new VoiceSubmissionError('not_sent'));
  store.selectOwner(null);
  assert.equal(store.read('owner', 'chat'), null);
  store.selectOwner('owner');
  assert.equal(store.retain(scope, 'Late private words', new Error('late failure')), false);
  assert.equal(store.read('owner', 'chat'), null);
  store.selectOwner('another');
  assert.equal(store.scope('owner', 'chat'), null);
});

test('late failures and stale dismissals never overwrite or clear newer retained words', () => {
  const store = new VoiceRecoveryStore(); store.selectOwner('owner');
  const scope = store.scope('owner', 'chat');
  store.retain(scope, 'First retained words', new VoiceSubmissionError('not_sent'));
  const first = store.read('owner', 'chat');
  assert.equal(store.retain(scope, 'Late replacement', new Error('network')), false);
  assert.equal(store.read('owner', 'chat').text, first.text);
  assert.equal(store.dismiss('other', 'chat', first.id), false);
  assert.equal(store.dismiss('owner', 'chat', first.id), true);
  store.retain(scope, 'Next retained words', new Error('unknown'));
  assert.equal(store.dismiss('owner', 'chat', first.id), false);
  assert.equal(store.read('owner', 'chat').text, 'Next retained words');
});
