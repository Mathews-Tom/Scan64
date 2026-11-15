import { useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import { Button, Field, Panel, StatusLine } from './Primitives';

const CONFIRMATION = 'RESET PROGRESS';
const reloadApplication = () => window.location.reload();

export function ProgressReset({ reload = reloadApplication }: { reload?: () => void }) {
  const [stage, setStage] = useState<'idle' | 'confirm' | 'complete'>('idle');
  const [confirmation, setConfirmation] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const locked = useRef(false);

  async function reset() {
    if (stage !== 'confirm' || confirmation !== CONFIRMATION || locked.current) return;
    locked.current = true;
    setPending(true);
    setError(null);
    try {
      await ApiClient.resetProgress(confirmation);
      setStage('complete');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The server did not confirm the reset.');
      locked.current = false;
    } finally {
      setPending(false);
    }
  }

  return (
    <Panel title="Reset learning progress">
      <p>Start learning again without deleting your account or chess library. This cannot be undone.</p>
      <p><strong>Cleared:</strong> puzzle and lesson results, learning ratings and strength estimates, streaks, mastery, achievements and review schedules, including active learning sessions.</p>
      <p><strong>Kept:</strong> your account and profile, saved games and analyses, imported libraries, settings, appearance and configured goals.</p>
      {stage === 'idle' ? (
        <Button onClick={() => setStage('confirm')}>Reset learning progress…</Button>
      ) : stage === 'complete' ? (
        <>
          <StatusLine>Learning progress reset successfully. Your account, games, analyses, libraries and settings were kept.</StatusLine>
          <p>Continue to reload Scan64 with fresh learning state. Other open Scan64 tabs must also be reloaded before continuing.</p>
          <Button variant="primary" onClick={reload}>Continue and reload</Button>
        </>
      ) : (
        <div aria-busy={pending}>
          <Field
            label="Type RESET PROGRESS to confirm"
            hint="Use the exact uppercase phrase. Nothing is deleted until you press Permanently reset learning progress."
            value={confirmation}
            onChange={(event) => setConfirmation(event.target.value)}
            disabled={pending}
            autoComplete="off"
            spellCheck={false}
          />
          <Button
            variant="primary"
            disabled={pending || confirmation !== CONFIRMATION}
            onClick={() => { void reset(); }}
          >{pending ? 'Resetting learning progress…' : 'Permanently reset learning progress'}</Button>
          <Button disabled={pending} onClick={() => {
            setStage('idle');
            setConfirmation('');
            setError(null);
          }}>Cancel</Button>
          {pending ? <StatusLine>Reset in progress. Please wait for the server response.</StatusLine> : null}
          {error ? <StatusLine kind="error">Reset request failed: {error} No automatic retry was made. If the response was interrupted, the reset may already have completed; reload to check before trying again.</StatusLine> : null}
        </div>
      )}
    </Panel>
  );
}
