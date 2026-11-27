import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { Button, Panel, StatusLine } from './Primitives';
import { DEFAULT_NARRATOR_VOICE, NARRATOR_VOICES, probeLocalExplanations, readExplanationSettings, saveExplanationSettings, storedExplanationOwner } from '../api/explanationSettings';
import type { ExplanationSettings, LocalExplanationProbe, NarratorVoice } from '../api/explanationSettings';

const VOICE_LABELS: Record<NarratorVoice, string> = {
  coach: 'Coach: calm and encouraging (default)',
  terse: 'Terse: few words, no filler',
  playful: 'Playful: light and cheerful',
};

export function LocalExplanationSettings() {
  const [refreshGeneration, refresh] = useState(0);
  useEffect(() => {
    const changed = () => refresh(value => value + 1);
    window.addEventListener('storage', changed);
    window.addEventListener('focus', changed);
    return () => {
      window.removeEventListener('storage', changed);
      window.removeEventListener('focus', changed);
    };
  }, []);
  const playerId = storedExplanationOwner();
  return <Panel title="Local explanation wording">
    {playerId ? <OwnerSettings key={playerId} playerId={playerId} refreshGeneration={refreshGeneration} /> : <StatusLine>Disabled. Sign in with a player identity to manage optional local wording.</StatusLine>}
  </Panel>;
}

function OwnerSettings({ playerId, refreshGeneration }: { playerId: string; refreshGeneration: number }) {
  const [settings, setSettings] = useState<ExplanationSettings | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const [probe, setProbe] = useState<LocalExplanationProbe | null>(null);
  const [model, setModel] = useState('');
  const [voice, setVoice] = useState<NarratorVoice>(DEFAULT_NARRATOR_VOICE);
  const [consent, setConsent] = useState(false);
  const [activity, setActivity] = useState<'read' | 'action' | null>(null);
  const [error, setError] = useState('');
  const controller = useRef<{ abort: AbortController; kind: 'read' | 'action' } | null>(null);
  const refreshed = useRef<number | null>(null);
  const busy = activity !== null;
  const selectId = useId();
  const voiceId = useId();

  const beginRequest = useCallback((kind: 'read' | 'action', preemptRead = false) => {
    if (storedExplanationOwner() !== playerId) return null;
    const pending = controller.current;
    if (pending) {
      if (!preemptRead || pending.kind !== 'read') return null;
      pending.abort.abort();
    }
    const request = { abort: new AbortController(), kind };
    controller.current = request;
    const current = () => controller.current === request && !request.abort.signal.aborted && storedExplanationOwner() === playerId;
    setActivity(kind);
    setError('');
    setConsent(false);
    return {
      signal: request.abort.signal,
      current,
      finish: () => {
        if (controller.current !== request) return;
        controller.current = null;
        setActivity(null);
      },
    };
  }, [playerId]);

  useEffect(() => () => {
    controller.current?.abort.abort();
    controller.current = null;
    refreshed.current = null;
  }, [playerId]);

  useEffect(() => {
    // Coalesce focus changes during a request and read only after it finishes.
    if (activity !== null || refreshed.current === refreshGeneration) return;
    const request = beginRequest('read');
    if (!request) return;
    refreshed.current = refreshGeneration;
    void readExplanationSettings(playerId, request.signal).then(value => {
      if (request.current()) { setSettings(value); setUncertain(false); if (value.voice) setVoice(value.voice); }
    }).catch(() => {
      if (request.current()) setError('Could not read wording settings. Check your player sign-in and Scan64 connection, then refocus Settings.');
    }).finally(request.finish);
  }, [activity, beginRequest, playerId, refreshGeneration]);

  async function act(action: 'probe' | 'enable' | 'disable') {
    if (settings?.mode === 'operator' || storedExplanationOwner() !== playerId) return;
    if (action === 'enable' && (!consent || !model || !probe?.models.includes(model))) return;
    const request = beginRequest('action', action === 'disable' && uncertain);
    if (!request) return;
    try {
      if (action === 'probe') {
        setProbe(null);
        setModel('');
        const detected = await probeLocalExplanations(playerId, request.signal);
        if (request.current()) { setProbe(detected); setModel(detected.models[0] ?? ''); }
      } else {
        const saved = await saveExplanationSettings(playerId, action === 'disable' ? null : model, request.signal, voice);
        if (request.current()) { setSettings(saved); setUncertain(false); }
      }
    } catch {
      if (request.current()) {
        if (action !== 'probe') { setSettings(null); setUncertain(true); }
        setError(action === 'probe'
          ? 'Detection failed. Start local Ollama and check the Scan64 connection, then choose Detect local Ollama again.'
          : 'Could not confirm saved settings. Disable local wording to revoke consent, or refocus Settings to check the persisted status before generating explanations.');
      }
    } finally {
      request.finish();
    }
  }

  if (storedExplanationOwner() !== playerId) return <StatusLine>Disabled. Reopen Settings with your active player identity.</StatusLine>;
  return <>
    {uncertain ? <StatusLine>Persisted wording status is unknown. Disable local wording to revoke consent.</StatusLine> : settings ? <StatusLine>{settings.mode === 'operator' ? 'Managed by the operator. Local wording settings are read-only.' : settings.mode === 'local' ? `Enabled for new explanations using ${settings.model}${settings.voice ? ` with the ${settings.voice} voice` : ''}.` : 'Disabled. Explanations use the built-in templates.'}</StatusLine> : null}
    {busy ? <StatusLine>Loading wording settings…</StatusLine> : null}
    {error ? <StatusLine kind="error">{error}</StatusLine> : null}
    {(settings || uncertain) && settings?.mode !== 'operator' ? <>
      <p>Optional and off by default. Detection checks the local service without sending chess evidence; it does not enable wording or download models.</p>
      <Button disabled={busy} onClick={() => { void act('probe'); }}>Detect local Ollama</Button>
      {probe && !probe.available ? <StatusLine>Local Ollama is unavailable. Start Ollama on this device, then detect again.</StatusLine> : null}
      {probe?.available && probe.models.length === 0 ? <StatusLine>No compatible local models found. Install a local model in Ollama, then detect again.</StatusLine> : null}
      {probe?.available && probe.models.length > 0 ? <>
        <label htmlFor={selectId}>Local model</label>
        <select id={selectId} value={model} disabled={busy} onChange={event => { setModel(event.target.value); setConsent(false); }}>
          {probe.models.map(name => <option key={name} value={name}>{name}</option>)}
        </select>
        <label htmlFor={voiceId}>Narrator voice</label>
        <select id={voiceId} value={voice} disabled={busy} onChange={event => setVoice(event.target.value as NarratorVoice)}>
          {NARRATOR_VOICES.map(name => <option key={name} value={name}>{VOICE_LABELS[name]}</option>)}
        </select>
        <p>The voice changes only the tone of the words around the engine facts. Every fact still comes from the engine, and the same checks apply to every voice.</p>
        <p>The chosen <strong>local model service</strong> ({model}) receives verified chess evidence for new explanations. Ollama runs separately and can log or forward this evidence. Enable only if you trust the local service. Existing lessons are not rewritten. Timeouts or invalid results use the built-in template.</p>
        <label><input type="checkbox" checked={consent} disabled={busy} onChange={event => setConsent(event.target.checked)} /> I consent to sending verified chess evidence to this local model service.</label>
        <Button disabled={busy || !consent || !model} onClick={() => { void act('enable'); }}>Enable local wording</Button>
      </> : null}
      <Button disabled={uncertain ? activity === 'action' : busy || settings?.mode !== 'local'} onClick={() => { void act('disable'); }}>Disable local wording</Button>
    </> : null}
  </>;
}
