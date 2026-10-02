import { useId } from 'react';
import { useI18n } from './i18n';
export type Phase = 'idle' | 'thinking' | 'executing' | 'stopped';

// Open folds and negative space: a living mark rather than a shaded solid sphere.
export default function Orb({ phase }: { phase: Phase }) {
  const id = useId().replaceAll(':', '');
  const { t } = useI18n();
  return <div className={`orb-scene ${phase}`} role="img" aria-label={t(`orb.${phase}`)}>
    <div className="aura-bloom" />
    <svg className="aura" viewBox="0 0 240 240" fill="none" aria-hidden="true">
      <defs>
        <linearGradient id={`${id}-rose`} x1="48" y1="55" x2="176" y2="185" gradientUnits="userSpaceOnUse"><stop stopColor="#edb6de" /><stop offset=".43" stopColor="#d6a2f1" /><stop offset="1" stopColor="#aca2ee" stopOpacity=".3" /></linearGradient>
        <linearGradient id={`${id}-iris`} x1="178" y1="80" x2="60" y2="176" gradientUnits="userSpaceOnUse"><stop stopColor="#aea5ef" stopOpacity=".8" /><stop offset=".5" stopColor="#c79de4" /><stop offset="1" stopColor="#f0b3d8" stopOpacity=".3" /></linearGradient>
        <linearGradient id={`${id}-light`} x1="83" y1="63" x2="156" y2="168" gradientUnits="userSpaceOnUse"><stop stopColor="#fff" /><stop offset=".5" stopColor="#fff3fc" stopOpacity=".15" /><stop offset="1" stopColor="#f8daf0" /></linearGradient>
        <filter id={`${id}-soft`} x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="5" /></filter>
      </defs>
      <g className="aura-fold aura-back"><path d="M64 153C31 121 55 64 104 57C149 49 182 76 178 111C174 147 129 144 114 170C96 201 70 182 64 153Z" fill={`url(#${id}-iris)`} opacity=".3" filter={`url(#${id}-soft)`} /><path d="M64 153C31 121 55 64 104 57C149 49 182 76 178 111C174 147 129 144 114 170C96 201 70 182 64 153Z" stroke={`url(#${id}-iris)`} strokeWidth="2" opacity=".52" /></g>
      <g className="aura-fold aura-front"><path d="M62 147C55 104 100 55 138 66C175 77 203 129 178 159C153 189 97 179 88 147C80 119 107 96 131 110C154 123 143 158 118 163C90 169 65 165 62 147Z" fill={`url(#${id}-rose)`} fillRule="evenodd" opacity=".65" /><path d="M62 147C55 104 100 55 138 66C175 77 203 129 178 159C153 189 97 179 88 147C80 119 107 96 131 110" stroke={`url(#${id}-light)`} strokeWidth="1.4" /></g>
      <g className="aura-fold aura-ribbon"><path d="M77 79C111 56 171 97 169 130C167 165 103 193 78 165C58 143 94 119 120 124C147 129 151 156 140 174" stroke={`url(#${id}-iris)`} strokeWidth="13" strokeLinecap="round" opacity=".3" /><path d="M77 79C111 56 171 97 169 130C167 165 103 193 78 165" stroke={`url(#${id}-light)`} strokeWidth=".8" opacity=".9" /></g>
      <circle className="aura-seed" cx="121" cy="127" r="3" fill="#c7a2e2" />
    </svg>
  </div>;
}
