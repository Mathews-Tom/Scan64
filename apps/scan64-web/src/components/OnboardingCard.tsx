import { useEffect, useId, useRef, useState } from 'react';
import { ApiClient, getActivePlayerId } from '../api/client';
import type { DeclaredLevel, OnboardingAction, PlayerOnboardingRead } from '../api/types';
import './Strength.css';

export const DECLARED_LEVEL_LABELS: Record<DeclaredLevel, string> = {
  beginner: 'Beginner', developing: 'Developing', club: 'Club', advanced: 'Advanced', not_sure: 'Not sure',
};

export function OnboardingCard({ onNavigate, onChange }: {
  onNavigate?: (path: string) => void;
  onChange?: () => void;
}) {
  const playerId = getActivePlayerId();
  const id = useId();
  const [record, setRecord] = useState<PlayerOnboardingRead | null>(null);
  const [level, setLevel] = useState<DeclaredLevel>('not_sure');
  const [editing, setEditing] = useState(false);
  const [deferred, setDeferred] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);
  const [notice, setNotice] = useState('');
  const heading = useRef<HTMLHeadingElement>(null);
  const selectedChoice = useRef<HTMLInputElement>(null);
  const busy = useRef(false);

  useEffect(() => {
    if (editing) selectedChoice.current?.focus();
  }, [editing]);

  useEffect(() => {
    let cancelled = false;
    void ApiClient.getPlayerOnboarding(playerId).then(result => {
      if (cancelled) return;
      setRecord(result);
      setLevel(result.declared_level ?? 'not_sure');
    }).catch(cause => {
      if (!cancelled) setError(cause instanceof Error ? cause.message : 'Could not load your introduction.');
    });
    return () => { cancelled = true; };
  }, [playerId, request]);

  const save = async (data: OnboardingAction) => {
    if (busy.current) return;
    busy.current = true;
    setPending(true); setError(null);
    try {
      const result = await ApiClient.updatePlayerOnboarding(playerId, data);
      setRecord(result); setEditing(false); setDeferred(data.action === 'continue_later');
      setNotice(data.action === 'declare'
        ? `Suggested bot level ${result.suggested_bot_level}. You can change it in Play.`
        : data.action === 'skip' ? 'Introduction skipped. Your identity and progress are kept.'
          : 'Continue whenever you like. Your identity and progress are kept.');
      heading.current?.focus();
      onChange?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not save your choice.');
    } finally { busy.current = false; setPending(false); }
  };
  const showChoices = record && !deferred && (editing || record.status === 'pending');
  const showProgress = record?.intro_started_at !== null && record?.intro_started_at !== undefined;

  return (
    <section className="study-panel onboarding-card" aria-labelledby={`${id}-title`} aria-busy={pending}>
      <h2 id={`${id}-title`} ref={heading} tabIndex={-1}>Your starting point</h2>
      <p role="status">{notice}</p>
      {error ? <div role="alert"><p>{error}</p>{!record ? <button onClick={() => { setError(null); setRequest(value => value + 1); }}>Retry introduction</button> : null}</div> : null}
      {!record && !error ? <p role="status">Loading your introduction…</p> : null}
      {record ? <>
        <p>Declared level: {record.declared_level ? DECLARED_LEVEL_LABELS[record.declared_level] : 'Not declared'}. Suggested bot level: {record.suggested_bot_level ?? 'None yet'}.</p>
        <p>This is a starting suggestion, not a placement test or chess Elo. You can always play and choose another bot level.</p>
        {showChoices ? <form onSubmit={event => { event.preventDefault(); void save({ action: 'declare', declared_level: level }); }}>
          <fieldset disabled={pending}>
            <legend>How would you describe your chess experience?</legend>
            {Object.entries(DECLARED_LEVEL_LABELS).map(([value, label]) => <label key={value} className="onboarding-choice">
              <input ref={level === value ? selectedChoice : undefined} type="radio" name={`${id}-level`} value={value} checked={level === value} onChange={() => setLevel(value as DeclaredLevel)} />
              {label}
            </label>)}
          </fieldset>
          <div className="strength-actions">
            <button type="submit" disabled={pending}>Save level and show suggestion</button>
            <button type="button" disabled={pending} onClick={() => void save({ action: 'skip' })}>Skip introduction</button>
            <button type="button" disabled={pending} onClick={() => void save({ action: 'continue_later' })}>Continue later</button>
          </div>
        </form> : <div className="strength-actions">
          <button disabled={pending} onClick={() => { setEditing(true); setDeferred(false); }}>{deferred ? 'Continue introduction' : record.status === 'not_offered' ? 'Set up my starting point' : record.declared_level ? 'Change declared level' : 'Continue introduction'}</button>
          {record.status === 'in_progress' && !deferred ? <>
            <button disabled={pending} onClick={() => void save({ action: 'skip' })}>Skip introduction</button>
            <button disabled={pending} onClick={() => void save({ action: 'continue_later' })}>Continue later</button>
          </> : null}
        </div>}
        {showProgress ? <div>
          <h3>Five introductory daily puzzles</h3>
          <label htmlFor={`${id}-progress`}>{record.intro_closed} of {record.intro_target} introductory puzzles closed</label>
          <progress id={`${id}-progress`} value={record.intro_closed} max={record.intro_target} />
          <p>{record.intro_started} started. Solved (with or without hints), failed or abandoned runs each close once. Progress carries across days; these puzzles do not determine your bot suggestion.</p>
          {record.status === 'completed' ? <p>Introduction complete. Keep practising at your own pace.</p> : <p>Short or empty daily supply? Continue another day, or play now.</p>}
          <a href="/puzzles" onClick={event => {
            if (!onNavigate || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault(); onNavigate('/puzzles');
          }}>Start/resume daily puzzles</a>
        </div> : null}
        <a href="/play" onClick={event => {
          if (!onNavigate || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
          event.preventDefault(); onNavigate('/play');
        }}>Play now</a>
      </> : null}
    </section>
  );
}
