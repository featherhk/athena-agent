/** Pure voice state: deterministic endpointing and streaming speech boundaries. */
export class VoiceEndpoint {
  private voiced = 0;
  private quiet = 0;
  private elapsed = 0;
  private active = false;
  private noise = .004;
  reset() { this.voiced = this.quiet = this.elapsed = 0; this.active = false; }
  update(rms: number, milliseconds: number, outputPlaying = false): 'start' | 'end' | null {
    const threshold = Math.max(outputPlaying ? .045 : .018, this.noise * (outputPlaying ? 5 : 3));
    if (!this.active && rms < threshold) this.noise = this.noise * .98 + rms * .02;
    if (rms >= threshold) { this.voiced += milliseconds; this.quiet = 0; }
    else { this.quiet += milliseconds; if (!this.active) this.voiced = 0; }
    if (!this.active && this.voiced >= 160) { this.active = true; this.elapsed = this.voiced; return 'start'; }
    if (this.active) {
      this.elapsed += milliseconds;
      if (this.quiet >= 750 || this.elapsed >= 29000) { this.reset(); return 'end'; }
    }
    return null;
  }
}

export function pcmWave(chunks: readonly Float32Array[], sampleRate: number): Blob {
  const count = chunks.reduce((n, chunk) => n + chunk.length, 0);
  const buffer = new ArrayBuffer(44 + count * 2), view = new DataView(buffer);
  const write = (offset: number, text: string) => { for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i)); };
  write(0, 'RIFF'); view.setUint32(4, 36 + count * 2, true); write(8, 'WAVE'); write(12, 'fmt ');
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true); view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); write(36, 'data'); view.setUint32(40, count * 2, true);
  let offset = 44;
  for (const chunk of chunks) for (const value of chunk) { const sample = Math.max(-1, Math.min(1, value)); view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true); offset += 2; }
  return new Blob([buffer], { type: 'audio/wav' });
}

export function spokenText(text: string): string {
  return text.replace(/```[\s\S]*?(?:```|$)/g, '').replace(/<[^>]*>/g, '')
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '').replace(/^\s*(?:#{1,6}|[-*>]|\d+\.)\s*/gm, '')
    .replace(/[`*_~]/g, '').replace(/\s+/g, ' ').trim();
}

export class SpeechCursor {
  private offsets = new Map<string, number>();
  private ignored = new Set<string>();
  ignore(id: string) { this.ignored.add(id); }
  prime(messages: readonly { id: string; content: string }[]) { for (const item of messages) this.ignore(item.id); }
  take(id: string, content: string, complete: boolean): string[] {
    if (this.ignored.has(id)) return [];
    const offset = this.offsets.get(id) || 0, rest = content.slice(offset);
    // Wait for punctuation; cap long unpunctuated replies to bounded TTS calls.
    const end = complete ? rest.length : [...rest.matchAll(/[.!?。！？；;\n](?:\s|$)?/g)].at(-1)?.index;
    const limit = end === undefined ? (rest.length >= 320 ? 280 : 0) : complete ? end : end + 1;
    if (!limit) return [];
    const consumed = rest.slice(0, limit);
    // An open code fence is withheld until closed; no spoken source code.
    if (!complete && (consumed.match(/```/g)?.length || 0) % 2) return [];
    this.offsets.set(id, offset + limit);
    const plain = spokenText(consumed);
    return plain ? plain.match(/[\s\S]{1,500}(?:\s|$)|[\s\S]{1,500}/g) || [] : [];
  }
}
