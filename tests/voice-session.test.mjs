import test from 'node:test';
import assert from 'node:assert/strict';
import { VoiceSession } from '../src/voice-session.ts';

const tick = () => new Promise(resolve => setImmediate(resolve));
function browser() {
  const originals = new Map(['window', 'navigator', 'AudioContext', 'AudioWorkletNode', 'fetch'].map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const tracks = [{ stopped: false, enabled: true, stop() { this.stopped = true; } }];
  const media = { getTracks: () => tracks, getAudioTracks: () => tracks };
  let capture, speaker, permissionCalls = 0;
  class Context {
    sampleRate = 16000; state = 'running'; destination = {};
    audioWorklet = { addModule: async () => {} };
    async resume() {}
    async close() { this.state = 'closed'; }
    createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
    async decodeAudioData() { return {}; }
    createBufferSource() {
      speaker = { stopped: false, connect() {}, disconnect() {}, start() {}, stop() { this.stopped = true; this.onended?.(); } };
      return speaker;
    }
  }
  class Capture {
    port = { onmessage: undefined };
    constructor() { capture = this; }
    connect() {}
    disconnect() {}
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: new EventTarget() });
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { mediaDevices: { getUserMedia: async () => { permissionCalls++; return media; } } } });
  globalThis.AudioContext = Context; globalThis.AudioWorkletNode = Capture;
  return {
    tracks, media, get speaker() { return speaker; }, get permissionCalls() { return permissionCalls; },
    samples(level, count) { for (let i = 0; i < count; i++) capture.port.onmessage({ data: new Float32Array(1600).fill(level) }); },
    restore() { for (const [key, descriptor] of originals) { if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key]; } },
  };
}

test('an unavailable service never opens the microphone', async () => {
  const env = browser(), states = [], errors = [];
  globalThis.fetch = async () => Response.json({ available: false });
  try {
    const call = new VoiceSession('sid', 'en', { state: value => states.push(value), error: value => errors.push(value), transcript() {}, submit: async () => {}, interrupt: async () => {} });
    await call.start([]);
    assert.equal(env.permissionCalls, 0);
    assert.deepEqual(errors, ['voice_unavailable']);
    assert.equal(states.at(-1), 'ended');
  } finally { env.restore(); }
});

test('hanging up while microphone permission is pending closes the eventual stream', async () => {
  const env = browser(); let allow;
  globalThis.fetch = async () => Response.json({ available: true });
  navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { allow = resolve; });
  try {
    const call = new VoiceSession('sid', 'en', { state() {}, error() {}, transcript() {}, submit: async () => assert.fail('unexpected submission'), interrupt: async () => {} });
    const pending = call.start([]); await tick(); call.end(); allow(env.media); await pending;
    assert.equal(env.tracks[0].stopped, true);
  } finally { env.restore(); }
});

test('handsfree turns submit once and barge-in stops playback and ignores late old-run output', async () => {
  const env = browser(), submissions = [], synthesis = []; let interrupts = 0;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/voice') return Response.json({ available: true });
    if (url.includes('/transcribe')) return Response.json({ text: 'Spoken request' });
    synthesis.push(JSON.parse(options.body).text);
    return new Response(new Uint8Array([1, 2, 3]));
  };
  let call;
  try {
    call = new VoiceSession('sid', 'en', { state() {}, error: code => assert.fail(code), transcript() {}, submit: async text => submissions.push(text), interrupt: async () => { interrupts++; } });
    await call.start([{ id: 'history', content: 'Do not repeat.', role: 'assistant', status: 'complete' }]);
    call.update([], false);
    env.samples(.2, 4); env.samples(0, 8); await tick(); await tick();
    assert.deepEqual(submissions, ['Spoken request']);
    const reply = { id: 'reply', run_id: 'run1', role: 'assistant', content: 'Here is your answer.', status: 'streaming' };
    call.update([reply], true); await tick(); await tick();
    assert.deepEqual(synthesis, ['Here is your answer.']);
    env.samples(.2, 3); await tick();
    assert.equal(env.speaker.stopped, true);
    assert.equal(interrupts, 1);
    call.update([reply, { ...reply, id: 'later', content: 'Old tool loop replied.' }], true);
    env.samples(0, 8); await tick(); await tick();
    assert.equal(submissions.length, 2);
    call.update([reply, { ...reply, id: 'later', content: 'Old tool loop replied.' }], false); await tick();
    assert.equal(synthesis.length, 1);
  } finally { call?.end(); env.restore(); }
});

test('a late transcription after hangup cannot submit into the conversation', async () => {
  const env = browser(); let finish;
  globalThis.fetch = async url => url === '/api/voice' ? Response.json({ available: true }) : new Promise(resolve => { finish = resolve; });
  let call;
  try {
    call = new VoiceSession('sid', 'en', { state() {}, error() {}, transcript() {}, submit: async () => assert.fail('late transcript submitted'), interrupt: async () => {} });
    await call.start([]); env.samples(.2, 4); env.samples(0, 8); await tick();
    call.end(); finish(Response.json({ text: 'Too late' })); await tick(); await tick();
    assert.equal(env.tracks[0].stopped, true);
  } finally { call?.end(); env.restore(); }
});

test('quiet speech during synthesis interrupts without playing a late audio response', async () => {
  const env = browser(), states = []; let finishSpeech, speechSignal, interrupts = 0, call;
  globalThis.fetch = async (url, options) => {
    if (url === '/api/voice') return Response.json({ available: true });
    assert.ok(url.endsWith('/voice/speech'));
    speechSignal = options.signal;
    // Model an upstream reply arriving despite a locally aborted request.
    return new Promise(resolve => { finishSpeech = resolve; });
  };
  try {
    call = new VoiceSession('sid', 'en', { state: value => states.push(value), error: code => assert.fail(code), transcript() {}, submit: async () => assert.fail('utterance has not ended'), interrupt: async () => { interrupts++; } });
    await call.start([]);
    call.update([{ id: 'reply', run_id: 'run1', role: 'assistant', content: 'Here is your answer.', status: 'streaming' }], true);
    await tick();
    assert.ok(finishSpeech);
    assert.equal(env.speaker, undefined, 'synthesis is pending, so there is no speaker echo');
    env.samples(.03, 2); await tick();
    assert.equal(interrupts, 1);
    assert.equal(speechSignal.aborted, true);
    assert.equal(states.at(-1), 'hearing');
    finishSpeech(new Response(new Uint8Array([1, 2, 3]))); await tick(); await tick();
    assert.equal(env.speaker, undefined, 'cancelled synthesis must never start playback');
    call.end();
    assert.equal(env.tracks[0].stopped, true);
    assert.equal(states.at(-1), 'ended');
  } finally { call?.end(); env.restore(); }
});

test('weak echo is suppressed only while audio plays, and natural completion clears the threshold', async () => {
  const env = browser(); let interrupts = 0, call;
  globalThis.fetch = async url => url === '/api/voice' ? Response.json({ available: true }) : new Response(new Uint8Array([1, 2, 3]));
  try {
    call = new VoiceSession('sid', 'en', { state() {}, error: code => assert.fail(code), transcript() {}, submit: async () => assert.fail('utterance has not ended'), interrupt: async () => { interrupts++; } });
    await call.start([]);
    call.update([{ id: 'reply', run_id: 'run1', role: 'assistant', content: 'A reply.', status: 'streaming' }], true);
    await tick(); await tick();
    const speaker = env.speaker;
    assert.ok(speaker);
    env.samples(.03, 5); await tick();
    assert.equal(interrupts, 0, 'weak speaker echo must not interrupt actual playback');
    assert.equal(speaker.stopped, false);
    speaker.onended(); await tick();
    env.samples(.03, 2); await tick();
    assert.equal(interrupts, 1, 'the same quiet voice can interrupt thinking after playback ends');
  } finally { call?.end(); env.restore(); }
});

test('cancelling playback clears the echo threshold for the next utterance and hangup is final', async () => {
  const env = browser(), submissions = [], states = []; let interrupts = 0, call;
  globalThis.fetch = async url => url === '/api/voice' ? Response.json({ available: true })
    : url.includes('/transcribe') ? Response.json({ text: 'Another question' }) : new Response(new Uint8Array([1, 2, 3]));
  try {
    call = new VoiceSession('sid', 'en', { state: value => states.push(value), error: code => assert.fail(code), transcript() {}, submit: async text => submissions.push(text), interrupt: async () => { interrupts++; } });
    await call.start([]);
    call.update([{ id: 'reply', run_id: 'run1', role: 'assistant', content: 'A reply.', status: 'streaming' }], true);
    await tick(); await tick();
    const speaker = env.speaker;
    env.samples(.2, 2); await tick();
    assert.equal(speaker.stopped, true);
    assert.equal(interrupts, 1);
    env.samples(0, 8); await tick(); await tick();
    assert.deepEqual(submissions, ['Another question']);
    env.samples(.03, 2); await tick();
    assert.equal(interrupts, 2, 'cancelled playback must not leave the raised threshold active');
    call.end();
    const stateCount = states.length;
    env.samples(.2, 20); await tick();
    assert.equal(interrupts, 2);
    assert.equal(states.length, stateCount);
    assert.equal(states.at(-1), 'ended');
    assert.equal(env.tracks[0].stopped, true);
  } finally { call?.end(); env.restore(); }
});

test('native background ends microphone acquisition without cancelling server work', async () => {
  const env = browser(), states = []; let allow, interruptions = 0;
  globalThis.fetch = async () => Response.json({available:true});
  navigator.mediaDevices.getUserMedia = () => new Promise(resolve => { allow=resolve; });
  const call = new VoiceSession('sid','en',{state:value=>states.push(value),error:code=>assert.fail(code),transcript(){},submit:async()=>assert.fail('background submission'),interrupt:async()=>{interruptions++;}});
  try {
    const pending=call.start([]); await tick();
    window.dispatchEvent(new Event('athena:background'));
    allow(env.media); await pending;
    assert.equal(env.tracks[0].stopped,true);
    assert.equal(states.at(-1),'ended');
    assert.equal(interruptions,0);
    const count=states.length;
    window.dispatchEvent(new Event('athena:background'));
    assert.equal(states.length,count,'the ended call detached its native lifecycle listener');
  } finally {call.end();env.restore();}
});
