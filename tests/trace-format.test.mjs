import assert from 'node:assert/strict';
import test from 'node:test';
import { appendTracePage, charCount, traceContent, toolResult, toolRequests } from '../src/trace-format.ts';

test('tool records do not turn a failed operation into a successful message', () => {
  for (const result of [{ exit_code: 2 }, { ok: false }, { success: false }, { error: 'Permission denied' },
    { error: { code: 'unavailable' } }, { error_code: 'not_found' }, { status: 'failed' }, { status: 'timed_out' }]) {
    assert.equal(toolResult(JSON.stringify(result), 'complete').outcome, 'failed', JSON.stringify(result));
  }
  assert.equal(toolResult('{"ok":false,"effect_known":false}', 'complete').outcome, 'uncertain');
  assert.equal(toolResult('{"exit_code":-15,"cancelled":true}', 'complete').outcome, 'cancelled');
  assert.equal(toolResult('', 'failed').outcome, 'failed');
  const result = toolResult(JSON.stringify({ exit_code: 2, error: 'Permission denied', output: 'Could not open the document.' }), 'complete');
  assert.equal(result.exitCode, 2);
  assert.equal(result.message, 'Permission denied');
  assert.equal(result.output, 'Could not open the document.');
});

test('unknown and partial results stay neutral; transport completion is not task success', () => {
  for (const content of ['not JSON', '{"exit_code":0,"output":"truncated', '{"data":{"error":"quoted source text"}}',
    '{"exit_code":0,"error":null}', '{"ok":true}', '{"status":"registered"}']) {
    assert.equal(toolResult(content, 'complete').outcome, 'received', content);
  }
});

test('tool requests give a compact operation name while keeping raw arguments separate', () => {
  assert.deepEqual(toolRequests(JSON.stringify([{ function: { name: 'runtime', arguments: JSON.stringify({ op: 'browser', args: { action: 'click', secret: 'not a label' } }) } },
    { function: { name: 'exec', arguments: '{"command":"private content"}' } }])), ['runtime · browser · click', 'exec']);
  assert.deepEqual(toolRequests('[{"function":'), []);
  assert.deepEqual(toolRequests('{}'), []);
});

test('growing streamed previews retain fetched reasoning without duplication or missing Unicode', () => {
  const preview = '先看🙂', loaded = '先看🙂完整的思考';
  assert.equal(charCount(preview), 3);
  assert.equal(traceContent('先看🙂完整', loaded), loaded);
  assert.equal(traceContent(loaded + '。', loaded), loaded + '。');
  // The preview grew after the request started at its old, shorter boundary.
  const current = traceContent('先看🙂完整', preview);
  assert.equal(appendTracePage(current, { offset: 3, content: '完整的思考', total_chars: 8 }), loaded);
  // A late overlapping page does not append the same text a second time.
  assert.equal(appendTracePage(loaded, { offset: 3, content: '完整', total_chars: 8 }), loaded);
});

test('changed records and disjoint pages cannot silently splice unrelated reasoning', () => {
  assert.equal(traceContent('new record', 'old record with extra text'), 'new record');
  assert.equal(appendTracePage('abc', { offset: 4, content: 'e', total_chars: 5 }), undefined);
  assert.equal(appendTracePage('abc', { offset: 1, content: 'different', total_chars: 10 }), undefined);
  assert.equal(appendTracePage('abc', { offset: 3, content: 'tail', total_chars: 4 }), undefined);
});
