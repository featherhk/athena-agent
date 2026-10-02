import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceEndpoint, pcmWave, SpeechCursor, spokenText } from '../src/voice-core.ts';

test('voice endpoint rejects brief noise, confirms speech, waits for natural pause', () => {
  const vad = new VoiceEndpoint();
  assert.equal(vad.update(.08, 80), null);
  assert.equal(vad.update(.002, 40), null);
  assert.equal(vad.update(.08, 80), null);
  assert.equal(vad.update(.08, 80), 'start');
  assert.equal(vad.update(.002, 700), null);
  assert.equal(vad.update(.08, 100), null); // pause within a sentence
  assert.equal(vad.update(.002, 750), 'end');
  assert.equal(vad.update(.002, 800), null);
});

test('output echo has a higher threshold, prolonged speech remains bounded', () => {
  const vad = new VoiceEndpoint();
  for (let i = 0; i < 20; i++) assert.equal(vad.update(.03, 50, true), null);
  assert.equal(vad.update(.2, 200, true), 'start');
  assert.equal(vad.update(.2, 29000), 'end');
});

test('PCM encoder emits a valid bounded mono WAV and clamps samples', async () => {
  const audio = pcmWave([new Float32Array([-2, -.5]), new Float32Array([0, .5, 2])], 48000);
  const bytes = await audio.arrayBuffer(), view = new DataView(bytes);
  assert.equal(audio.type, 'audio/wav');
  assert.equal(bytes.byteLength, 54);
  assert.equal(new TextDecoder().decode(bytes.slice(0, 4)), 'RIFF');
  assert.equal(view.getUint32(24, true), 48000);
  assert.equal(view.getUint16(22, true), 1);
  assert.equal(view.getInt16(44, true), -32768);
  assert.equal(view.getInt16(52, true), 32767);
});

test('incremental speech never rereads history or a cancelled reply', () => {
  const cursor = new SpeechCursor();
  cursor.prime([{ id: 'old', content: 'Do not read.' }]);
  assert.deepEqual(cursor.take('old', 'Do not read.', true), []);
  assert.deepEqual(cursor.take('new', '你好', false), []);
  assert.deepEqual(cursor.take('new', '你好。还有', false), ['你好。']);
  assert.deepEqual(cursor.take('new', '你好。还有', false), []);
  assert.deepEqual(cursor.take('new', '你好。还有一个结果', true), ['还有一个结果']);
  assert.deepEqual(cursor.take('new', '你好。还有一个结果', true), []);
  cursor.ignore('cancelled');
  assert.deepEqual(cursor.take('cancelled', 'New delayed tokens.', true), []);
});

test('speech removes source code and formatting and bounds provider chunks', () => {
  assert.equal(spokenText('## Hello\n**world** [link](https://test.invalid)\n```js\nalert(1)\n```'), 'Hello world link');
  const cursor = new SpeechCursor();
  assert.deepEqual(cursor.take('code', '```js\nalert(1);', false), []);
  assert.deepEqual(cursor.take('code', '```js\nalert(1);\n```\nDone.', true), ['Done.']);
  const chunks = cursor.take('long', 'word '.repeat(1000), true);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every(chunk => chunk.length <= 600));
  assert.equal(chunks.join('').trim(), ('word '.repeat(1000)).trim());
});
