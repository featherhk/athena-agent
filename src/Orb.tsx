import { useId } from 'react';

export type Phase = 'idle' | 'thinking' | 'responding' | 'executing' | 'stopped';

// A folded ribbon, not a literal face. Shape and rhythm both encode state;
// the fixed shell supplies a readable status when animation is reduced.
export default function Orb({ phase }: { phase: Phase }) {
  const id = useId().replaceAll(':', '');
  return <div className={`orb-scene ${phase}`} aria-hidden="true">
    <div className="presence-glow" />
    <svg className="presence-art" viewBox="0 0 200 160" fill="none">
      <defs>
        <linearGradient id={`${id}-silk`} x1="44" y1="20" x2="151" y2="132" gradientUnits="userSpaceOnUse"><stop stopColor="#ec9dbe" /><stop offset=".42" stopColor="#b58ce0" /><stop offset="1" stopColor="#777bdd" /></linearGradient>
        <linearGradient id={`${id}-edge`} x1="46" y1="46" x2="130" y2="134" gradientUnits="userSpaceOnUse"><stop stopColor="#fff5ff" /><stop offset=".5" stopColor="#e6bfe8" /><stop offset="1" stopColor="#aea3f0" /></linearGradient>
      </defs>
      <g className="ribbon-orbit">
        {[0, 1, 2, 3].map(index => <g key={index} className={`ribbon ribbon-${index}`}>
          <path d="M99 80C61 84 39 61 51 44C62 27 93 42 103 65C112 86 102 110 81 117C54 126 39 103 57 87C70 75 84 76 99 80Z" fill={`url(#${id}-silk)`} fillOpacity=".36" stroke={`url(#${id}-silk)`} strokeWidth="1" />
          <path d="M99 80C61 84 39 61 51 44C62 27 93 42 103 65" stroke={`url(#${id}-edge)`} strokeWidth="1.8" />
        </g>)}
      </g>
      <g className="work-orbit"><ellipse cx="100" cy="80" rx="72" ry="43" stroke={`url(#${id}-silk)`} strokeWidth="1" strokeDasharray="3 7" /><circle cx="172" cy="80" r="4" fill="#a482d8" /></g>
      <g className="voice-bars">{[0, 1, 2, 3, 4].map(index => <rect key={index} className={`voice-bar bar-${index}`} x={79 + index * 9} y={66 + Math.abs(index - 2) * 4} width="5" height={28 - Math.abs(index - 2) * 8} rx="2.5" fill={`url(#${id}-silk)`} />)}</g>
      <g className="pause-mark"><path d="M94 74v12m12-12v12" stroke="#95869f" strokeWidth="3" strokeLinecap="round" /></g>
    </svg>
  </div>;
}
