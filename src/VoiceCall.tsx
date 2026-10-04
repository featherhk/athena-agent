import { useEffect, useRef, useState } from 'react';
import type { Message } from './api';
import { useI18n } from './i18n';
import { VoiceSession } from './voice-session';
import type { VoiceState } from './voice-session';
import { VoiceRecoveryStore, VoiceSubmissionError } from './voice-recovery';
import './voice.css';

type Props = {
  sessionId: string | null;
  ownerId: string | null;
  messages: readonly Message[];
  busy: boolean;
  disabled?: boolean;
  ensureSession: () => Promise<string | null>;
  submit: (text: string) => Promise<void>;
  interrupt: () => Promise<void>;
  onActiveChange?: (active: boolean) => void;
};

function Phone({ ended = false }: { ended?: boolean }) {
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d={ended ? 'M3 13c5-5 13-5 18 0l-2 4-5-2v-3h-4v3l-5 2-2-4Z' : 'M6 3h4l1 5-3 2c1 3 3 5 6 6l2-3 5 1v4c0 2-2 3-4 3C9 20 4 15 3 7c0-2 1-4 3-4Z'} /></svg>;
}

export default function VoiceCall(props: Props) {
  const { t, locale } = useI18n();
  const [state, setState] = useState<VoiceState>('ended');
  const [problem, setProblem] = useState('');
  const [transcript, setTranscript] = useState('');
  const [muted, setMuted] = useState(false);
  const [, refreshRecovery] = useState(0);
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const recovery = useRef(new VoiceRecoveryStore());
  const recoveryText = useRef<HTMLTextAreaElement>(null);
  const call = useRef<VoiceSession | null>(null);
  const callOwner = useRef<string | null>(null);
  const ticket = useRef(0);
  const current = useRef(props); current.current = props;
  const retained = recovery.current.read(props.ownerId, props.sessionId);
  const active = state !== 'ended';
  function end() { ticket.current++; call.current?.end(); call.current = null; callOwner.current = null; setState('ended'); }
  useEffect(() => () => { ticket.current++; call.current?.end(); recovery.current.selectOwner(null); }, []);
  useEffect(() => {
    // Also invalidate a call still waiting for ensureSession, before a microphone exists.
    const background = () => end();
    window.addEventListener('athena:background', background);
    return () => window.removeEventListener('athena:background', background);
  }, []);
  useEffect(() => {
    if (call.current && (props.sessionId !== call.current.sessionId || props.ownerId !== callOwner.current || !props.ownerId)) end();
  }, [props.sessionId, props.ownerId]);
  useEffect(() => {
    recovery.current.selectOwner(props.ownerId); setCopyState('idle'); setProblem(''); setTranscript(''); refreshRecovery(value => value + 1);
  }, [props.ownerId, props.sessionId]);
  useEffect(() => { props.onActiveChange?.(active); }, [active]);
  useEffect(() => { call.current?.update(props.messages, props.busy); }, [props.messages, props.busy]);
  async function start() {
    if (active || props.disabled || retained) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) { setProblem('voice.unsupported'); return; }
    setProblem(''); setTranscript(''); setMuted(false); setState('connecting');
    // Unlock playback before the first network await, while the click is active.
    const context = new AudioContext({ latencyHint: 'interactive' });
    void context.resume().catch(() => {});
    const generation = ++ticket.current;
    const owner = props.ownerId;
    try {
      const sid = await props.ensureSession();
      if (generation !== ticket.current || current.current.ownerId !== owner || !sid) { await context.close(); if (generation === ticket.current) setState('ended'); return; }
      const ownsCall = () => generation === ticket.current && current.current.ownerId === owner && current.current.sessionId === sid;
      recovery.current.selectOwner(current.current.ownerId);
      const recoveryScope = owner ? recovery.current.scope(owner, sid) : null;
      const voice = new VoiceSession(sid, locale, {
        state: value => { if (ownsCall()) setState(value); },
        error: code => { if (ownsCall()) setProblem(voiceError(code)); },
        transcript: value => { if (ownsCall()) setTranscript(value); },
        recover: (text, delivery) => {
          // Logout retires outstanding promises as well as visible recovery data.
          recovery.current.selectOwner(current.current.ownerId);
          if (recovery.current.retain(recoveryScope, text, new VoiceSubmissionError(delivery))) refreshRecovery(value => value + 1);
        },
        submit: text => ownsCall() ? current.current.submit(text) : Promise.reject(new VoiceSubmissionError('not_sent')),
        interrupt: () => ownsCall() ? current.current.interrupt() : Promise.reject(new Error('voice_interrupted')),
      }, context);
      call.current = voice;
      callOwner.current = owner;
      await voice.start(current.current.messages);
      voice.update(current.current.messages, current.current.busy);
    } catch { await context.close().catch(() => {}); if (generation === ticket.current) { setProblem('voice.failed'); end(); } }
  }
  return <div className={`voice-control${active ? ' active' : ''}`}>
    {active ? <div className="voice-call" role="region" aria-label={t('voice.title')}>
      <div className="voice-call-copy"><span className={`voice-wave ${state}`} aria-hidden="true"><i /><i /><i /><i /><i /></span><span role="status">{t(`voice.${state}`)}</span></div>
      {transcript && <p className="voice-transcript">{transcript}</p>}
      <div className="voice-call-actions"><button type="button" aria-pressed={muted} onClick={() => { const next = !muted; setMuted(next); call.current?.mute(next); }}>{t(muted ? 'voice.unmute' : 'voice.mute')}</button><button className="voice-end" type="button" onClick={end} aria-label={t('voice.end')}><Phone ended /></button></div>
      <small>{t('voice.disclosure')}</small>
    </div> : <button className="voice-start" type="button" aria-label={t('voice.start')} title={t('voice.start')} disabled={props.disabled || Boolean(retained)} onClick={() => void start()}><Phone /></button>}
    {retained && <section className="voice-recovery" role="region" aria-label={t('voice.recoveryTitle')}>
      <p role="status">{t(retained.delivery === 'not_sent' ? 'voice.notSent' : 'voice.deliveryUnknown')}</p>
      <textarea ref={recoveryText} readOnly value={retained.text} aria-label={t('voice.recoveryTitle')} rows={3} />
      <div><button type="button" onClick={async () => {
        const owner = props.ownerId, sid = props.sessionId, id = retained.id;
        try {
          await navigator.clipboard.writeText(retained.text);
          if (current.current.ownerId === owner && current.current.sessionId === sid && recovery.current.read(owner, sid)?.id === id) setCopyState('copied');
        } catch {
          if (current.current.ownerId === owner && current.current.sessionId === sid && recovery.current.read(owner, sid)?.id === id) { setCopyState('failed'); recoveryText.current?.focus(); recoveryText.current?.select(); }
        }
      }}>{t(copyState === 'copied' ? 'voice.copied' : 'voice.copy')}</button><button type="button" onClick={() => {
        recovery.current.dismiss(props.ownerId, props.sessionId, retained.id); setProblem(''); setCopyState('idle'); refreshRecovery(value => value + 1);
      }}>{t('voice.dismiss')}</button></div>
      {copyState === 'failed' && <small role="status">{t('voice.copyFailed')}</small>}
    </section>}
    {problem && !retained && <div className="voice-problem" role="alert"><span>{t(problem as Parameters<typeof t>[0])}</span><button type="button" onClick={() => setProblem('')} aria-label={t('close')}>×</button></div>}
  </div>;
}

function voiceError(code: string) {
  if (code === 'voice_unavailable') return 'voice.unavailable';
  if (code === 'voice_microphone') return 'voice.microphone';
  if (code === 'voice_suspended') return 'voice.suspended';
  if (code === 'voice_language') return 'voice.language';
  if (['account_balance_insufficient', 'balance_insufficient', 'credit_insufficient', 'account_not_enabled'].includes(code)) return 'voice.balance';
  return 'voice.failed';
}
