// Microphone samples only. The main thread owns VAD, transport and lifecycle.
class AthenaCapture extends AudioWorkletProcessor {
  constructor() { super(); this.samples = new Float32Array(1024); this.used = 0; }
  process(inputs) {
    const channel = inputs[0]?.[0];
    if (channel) for (const sample of channel) {
      this.samples[this.used++] = sample;
      if (this.used === this.samples.length) {
        this.port.postMessage(this.samples, [this.samples.buffer]);
        this.samples = new Float32Array(1024); this.used = 0;
      }
    }
    return true;
  }
}
registerProcessor('athena-capture', AthenaCapture);
