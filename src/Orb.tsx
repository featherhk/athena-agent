export type Phase = 'idle' | 'thinking' | 'responding' | 'executing' | 'stopped' | 'waiting';

/** An original line emblem informed by the Attic owl, without a coin frame. */
export function AthenaMark({ compact = false }: { compact?: boolean }) {
  return <svg className={compact ? 'brand-mark' : 'presence-art'} viewBox="0 0 96 96" fill="none" aria-hidden="true">
    <g className="presence-signature" stroke="currentColor" strokeWidth={compact ? 2.8 : 1.6} strokeLinecap="round" strokeLinejoin="round">
      <path d="M24 29L34 32C42 29 54 29 62 32L72 29L68 44C72 60 63 76 48 77C33 76 24 62 28 45Z" />
      <path d="M26 40C31 32 40 33 45 40C39 49 30 48 26 40ZM51 40C57 32 66 33 70 40C66 48 57 49 51 40Z" />
      <circle cx="35.5" cy="40" r={compact ? 2.4 : 2} fill="currentColor" stroke="none" />
      <circle cx="61" cy="40" r={compact ? 2.4 : 2} fill="currentColor" stroke="none" />
      <path d="M44 47L48 54L52 47M35 76L32 81M43 78L42 82M53 78L54 82M60 76L63 81" />
      {!compact && <>
        <path d="M31 50C30 61 35 68 41 71M35 53C35 59 38 65 43 67M62 51C66 60 61 69 54 72M59 55C62 61 58 65 53 67" />
        <path d="M47 60L49 62M47 67L49 69" />
      </>}
    </g>
  </svg>;
}

export default function Orb({ phase }: { phase: Phase }) {
  return <div className={`orb-scene ${phase}`} aria-hidden="true">
    <AthenaMark />
    <svg className="presence-state" viewBox="0 0 96 96" fill="none">
      <g className="presence-trace"><path d="M22 48a26 26 0 0 1 43-20" /><path d="M74 48a26 26 0 0 1-43 20" /></g>
      <g className="presence-work"><path d="M28 36h40M28 48h40M28 60h40" /></g>
      <g className="presence-voice">{[0, 1, 2, 3, 4].map(index => <path key={index} className={`voice-bar bar-${index}`} d={`M${32 + index * 8} ${38 + Math.abs(index - 2) * 3}v${20 - Math.abs(index - 2) * 6}`} />)}</g>
      <g className="presence-pause"><path d="M43 40v16M53 40v16" /></g>
      <g className="presence-wait"><circle cx="48" cy="48" r="3" /><circle cx="34" cy="48" r="2" /><circle cx="62" cy="48" r="2" /></g>
    </svg>
  </div>;
}
