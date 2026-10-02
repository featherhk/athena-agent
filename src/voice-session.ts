import { VoiceEndpoint, pcmWave, SpeechCursor } from './voice-core.ts';
import type { Message } from './api';

export type VoiceState = 'connecting' | 'listening' | 'hearing' | 'transcribing' | 'thinking' | 'speaking' | 'muted' | 'ended';
export type VoiceCallbacks = {
  state: (value: VoiceState) => void;
  error: (code: string) => void;
  transcript: (text: string) => void;
  submit: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
};

async function checked(response: Response) {
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.code === 'string' ? body.code : 'voice_provider_error');
  }
  return response;
}

export class VoiceSession {
  readonly sessionId: string;
  private language: string;
  public callbacks: VoiceCallbacks;
  private context: AudioContext;
  private stream?: MediaStream;
  private capture?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private speaker?: AudioBufferSourceNode;
  private detector = new VoiceEndpoint();
  private cursor = new SpeechCursor();
  private pre: Float32Array[] = [];
  private chunks: Float32Array[] = [];
  private recording = false;
  private ended = false;
  private muted = false;
  private outputQueue: string[] = [];
  private inputQueue: Blob[] = [];
  private speechEpoch = 0;
  private speaking = false;
  private transcribing = false;
  private speechRequest?: AbortController;
  private inputRequest?: AbortController;
  private busy = false;
  private holdOutput = false;
  private latest: readonly Message[] = [];
  private interruptedRuns = new Set<string>();
  private interrupting = Promise.resolve();
  private readonly onPageHide = () => this.end();

  constructor(sessionId: string, language: string, callbacks: VoiceCallbacks, context?: AudioContext) {
    this.sessionId = sessionId; this.language = language; this.callbacks = callbacks;
    // Construct synchronously inside the call button's user gesture for Safari.
    this.context = context || new AudioContext({ latencyHint: 'interactive' });
    void this.context.resume().catch(() => {});
  }

  async start(messages: readonly Message[]) {
    this.cursor.prime(messages.filter(message => !message.id.startsWith('voice-help:'))); this.latest = messages; this.callbacks.state('connecting');
    try {
      const response = await checked(await fetch('/api/voice'));
      const service = await response.json();
      if (!service.available) throw new Error('voice_unavailable');
      if (service.unsupported_languages?.includes(this.language.split('-')[0])) throw new Error('voice_language');
      if (this.ended) return;
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 }, video: false });
      if (this.ended) { stream.getTracks().forEach(track => track.stop()); return; }
      this.stream = stream;
      for (const track of stream.getAudioTracks()) track.onended = () => this.fail('voice_microphone');
      await this.context.audioWorklet.addModule(new URL('./voice-capture.worklet.js', import.meta.url));
      if (this.ended) return;
      this.capture = new AudioWorkletNode(this.context, 'athena-capture');
      this.source = this.context.createMediaStreamSource(stream);
      this.source.connect(this.capture);
      // Worklet has a silent output to keep processing without monitoring the mic.
      this.capture.connect(this.context.destination);
      this.capture.port.onmessage = event => this.samples(event.data as Float32Array);
      this.context.onstatechange = () => {
        if (!this.ended && this.context.state === 'suspended') this.fail('voice_suspended');
      };
      window.addEventListener('pagehide', this.onPageHide);
      this.updateState();
    } catch (error) {
      const name = (error as Error).name;
      this.fail(name === 'NotAllowedError' || name === 'NotFoundError' ? 'voice_microphone' : (error as Error).message);
    }
  }

  private updateState() {
    if (this.ended) return;
    this.callbacks.state(this.muted ? 'muted' : this.recording ? 'hearing' : this.speaking ? 'speaking' : this.transcribing ? 'transcribing' : this.busy ? 'thinking' : 'listening');
  }

  private samples(samples: Float32Array) {
    if (this.ended || this.muted) return;
    const milliseconds = samples.length / this.context.sampleRate * 1000;
    let energy = 0; for (const sample of samples) energy += sample * sample;
    const event = this.detector.update(Math.sqrt(energy / samples.length), milliseconds, this.speaking);
    if (this.recording) this.chunks.push(samples);
    else {
      this.pre.push(samples);
      while (this.pre.length * milliseconds > 360) this.pre.shift();
    }
    if (event === 'start') {
      this.recording = true; this.chunks = this.pre; this.pre = []; this.holdOutput = true;
      for (const message of this.latest) {
        if (message.role === 'assistant' && message.run_id) this.interruptedRuns.add(message.run_id);
        this.cursor.ignore(message.id);
      }
      const shouldInterrupt = this.busy || this.speaking;
      this.cancelSpeech();
      if (shouldInterrupt) this.interrupting = this.interrupting.then(() => this.callbacks.interrupt()).catch(() => this.fail('voice_interrupted'));
      this.updateState();
    }
    if (event === 'end') {
      this.recording = false;
      const audio = pcmWave(this.chunks, this.context.sampleRate); this.chunks = []; this.pre = [];
      if (this.inputQueue.length >= 3) { this.fail('voice_busy'); return; }
      this.inputQueue.push(audio); void this.transcribe(); this.updateState();
    }
  }

  private async transcribe() {
    if (this.transcribing || this.ended) return;
    this.transcribing = true; this.updateState();
    try {
      while (this.inputQueue.length && !this.ended) {
        const audio = this.inputQueue.shift()!;
        const request = new AbortController(); this.inputRequest = request;
        const response = await checked(await fetch(`/api/sessions/${encodeURIComponent(this.sessionId)}/voice/transcribe?language=${encodeURIComponent(this.language.split('-')[0])}`, {
          method: 'POST', headers: { 'Content-Type': 'audio/wav' }, body: audio, signal: request.signal,
        }));
        const result = await response.json();
        if (this.ended) return;
        if (typeof result.text === 'string' && result.text.trim()) {
          this.callbacks.transcript(result.text);
          await this.interrupting;
          if (this.ended) return;
          // Empty/in-flight old assistant output was already ignored at barge-in.
          this.holdOutput = this.recording || this.inputQueue.length > 0;
          await this.callbacks.submit(result.text);
        } else this.holdOutput = this.recording || this.inputQueue.length > 0;
      }
    } catch (error) {
      if (!this.ended && (error as Error).name !== 'AbortError') this.fail((error as Error).message);
    } finally { this.transcribing = false; this.inputRequest = undefined; this.updateState(); }
  }

  update(messages: readonly Message[], busy: boolean) {
    this.busy = busy; this.latest = messages;
    if (this.ended) return;
    for (const message of messages) {
      if (message.role !== 'assistant') continue;
      if (this.holdOutput || (message.run_id && this.interruptedRuns.has(message.run_id)) || ['cancelled', 'interrupted', 'failed'].includes(message.status)) {
        this.cursor.ignore(message.id); continue;
      }
      this.outputQueue.push(...this.cursor.take(message.id, message.content, message.status === 'complete'));
    }
    if (this.outputQueue.length > 40) { this.fail('voice_busy'); return; }
    void this.speak(); this.updateState();
  }

  private async speak() {
    if (this.speaking || this.ended || this.holdOutput || !this.outputQueue.length) return;
    this.speaking = true; const epoch = this.speechEpoch; this.updateState();
    try {
      while (this.outputQueue.length && !this.ended && epoch === this.speechEpoch) {
        const text = this.outputQueue.shift()!;
        const request = new AbortController(); this.speechRequest = request;
        const response = await checked(await fetch(`/api/sessions/${encodeURIComponent(this.sessionId)}/voice/speech`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }), signal: request.signal,
        }));
        const buffer = await this.context.decodeAudioData(await response.arrayBuffer());
        if (this.ended || epoch !== this.speechEpoch) return;
        const speaker = this.context.createBufferSource(); speaker.buffer = buffer; speaker.connect(this.context.destination);
        this.speaker = speaker;
        await new Promise<void>(resolve => { speaker.onended = () => { speaker.disconnect(); resolve(); }; speaker.start(); });
        if (this.speaker === speaker) this.speaker = undefined;
      }
    } catch (error) {
      if (!this.ended && epoch === this.speechEpoch && (error as Error).name !== 'AbortError') this.fail((error as Error).message);
    } finally {
      if (epoch === this.speechEpoch) { this.speaking = false; this.speechRequest = undefined; this.updateState(); }
    }
  }

  private cancelSpeech() {
    this.speechEpoch++; this.outputQueue = []; this.speechRequest?.abort(); this.speechRequest = undefined;
    try { this.speaker?.stop(); } catch { /* Already ended. */ }
    this.speaker = undefined; this.speaking = false;
  }

  mute(value: boolean) {
    this.muted = value; this.detector.reset(); this.recording = false; this.chunks = []; this.pre = [];
    this.holdOutput = this.transcribing || this.inputQueue.length > 0;
    this.stream?.getAudioTracks().forEach(track => { track.enabled = !value; }); this.updateState();
  }

  end() {
    if (this.ended) return;
    this.ended = true; this.cancelSpeech(); this.inputRequest?.abort(); this.inputQueue = []; this.chunks = []; this.pre = [];
    this.capture?.disconnect(); this.source?.disconnect();
    this.stream?.getTracks().forEach(track => { track.onended = null; track.stop(); });
    void this.context.close().catch(() => {});
    window.removeEventListener('pagehide', this.onPageHide); this.callbacks.state('ended');
  }

  private fail(code: string) { this.callbacks.error(code); this.end(); }
}
