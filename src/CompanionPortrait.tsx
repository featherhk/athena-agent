import type { Phase } from './Orb';

/** A quiet sculptural owl, rendered locally; the registry may replace it. */
export default function CompanionPortrait({ phase, listening = false }: { phase: Phase; listening?: boolean }) {
  return <svg className={`companion-portrait ${phase}${listening ? ' listening' : ''}`} viewBox="0 0 120 128" fill="none" aria-hidden="true">
    <defs><linearGradient id="athena-feather" x1="26" y1="16" x2="99" y2="113" gradientUnits="userSpaceOnUse"><stop stopColor="#cfc5d2" /><stop offset=".5" stopColor="#9a879f" /><stop offset="1" stopColor="#6b5d78" /></linearGradient>
      <linearGradient id="athena-face" x1="45" y1="24" x2="76" y2="93" gradientUnits="userSpaceOnUse"><stop stopColor="#fffdf7" /><stop offset="1" stopColor="#e4dcd9" /></linearGradient></defs>
    <ellipse className="companion-shadow" cx="60" cy="119" rx="24" ry="2" fill="#65576d" opacity=".13" />
    <g className="companion-body">
      <path d="M28 26 24 10 44 23Q60 19 76 23L96 10 92 27Q103 46 96 69C94 98 78 113 60 113S26 98 24 69Q17 47 28 26Z" fill="url(#athena-feather)" />
      <path className="companion-wing left" d="M26 48C13 67 20 91 38 103 35 83 38 63 26 48Z" fill="#ac9daf" />
      <path className="companion-wing right" d="M94 48C107 67 100 91 82 103 85 83 82 63 94 48Z" fill="#8d7a97" />
      <g className="companion-face">
        <path d="M29 35C39 22 50 30 60 38 70 30 81 22 91 35 103 51 84 74 60 88 36 74 17 51 29 35Z" fill="url(#athena-face)" />
        <path d="M30 34C38 27 50 31 57 39M63 39C70 31 82 27 90 34" stroke="#b9aeba" strokeWidth="1.1" strokeLinecap="round" />
        <g className="companion-eyes"><ellipse cx="43" cy="49" rx="4" ry="6" fill="#453c50" /><ellipse cx="77" cy="49" rx="4" ry="6" fill="#453c50" /><circle cx="44" cy="47" r="1.2" fill="#fffdf7" /><circle cx="78" cy="47" r="1.2" fill="#fffdf7" /></g>
        <path d="m55 60 5 8 5-8-5-2Z" fill="#a39288" />
        <path d="m45 77 3 5m25-5-3 5M51 92l2 4m14-4-2 4m-6 3 1 3" stroke="#c8bbca" strokeWidth="1.2" strokeLinecap="round" />
      </g>
      <path d="m48 111-2 4m7-3v4m14-4v4m5-5 2 4" stroke="#726374" strokeWidth="1.8" strokeLinecap="round" />
    </g>
    <path className="companion-attention" d="M105 44q7 9 0 18M111 39q11 14 0 28" stroke="#bfa1bb" strokeWidth="1.5" strokeLinecap="round" />
  </svg>;
}
