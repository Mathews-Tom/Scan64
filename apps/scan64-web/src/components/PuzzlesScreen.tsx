import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient, PuzzleConflictError } from '../api/client';
import type { PuzzleRunRead, PuzzleStartRead } from '../api/types';
import { CoachBoard } from './CoachBoard';
import './PuzzlesScreen.css';

const IMPORT_COMMAND = 'uv run scan64-cli puzzles import /path/to/lichess_db_puzzle.csv.zst --json';

export function PuzzlesScreen() {
  const [run, setRun] = useState<PuzzleRunRead | null>(null);
  const [catalog, setCatalog] = useState<PuzzleStartRead | null>(null);
  const [pending, setPending] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState('Loading puzzle…');
  const [needsRefresh, setNeedsRefresh] = useState(false);
  const busy = useRef(false);
  const generation = useRef(0);

  const start = useCallback(async () => {
    if (busy.current) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    try {
      const result = await ApiClient.startPuzzleRun();
      if (request !== generation.current) return;
      setCatalog(result); setRun(result.run); setNeedsRefresh(false);
      setNotice(result.message);
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : 'Could not load puzzle.');
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  }, []);

  useEffect(() => {
    void start();
    return () => { generation.current += 1; busy.current = false; };
  }, [start]);

  const mutate = async (action: 'move' | 'hint' | 'abandon', move?: string) => {
    if (!run || run.state !== 'active' || busy.current || needsRefresh) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    const expected = { expected_cursor: run.cursor, expected_revision: run.revision };
    try {
      const next = action === 'move'
        ? await ApiClient.submitPuzzleMove(run.run_id, { ...expected, move: move! })
        : action === 'hint'
          ? await ApiClient.revealPuzzleHint(run.run_id, { ...expected, step: Math.min(3, run.hint_step + 1) as 1 | 2 | 3 })
          : await ApiClient.abandonPuzzleRun(run.run_id, expected);
      if (request !== generation.current) return;
      setRun(next);
      setNotice(action === 'move' && next.wrong_attempts > run.wrong_attempts
        ? next.state === 'active' ? 'That move is not the source continuation. Try again.' : 'That move is not the source continuation.'
        : action === 'move' && next.state === 'active' ? 'Source move accepted. Continue the puzzle.' : '');
    } catch (failure) {
      if (request !== generation.current) return;
      if (failure instanceof PuzzleConflictError) {
        setRun(failure.run);
        setNotice(`${failure.message} Latest puzzle state loaded; your action was not replayed.`);
      } else {
        setError(failure instanceof Error ? failure.message : 'Puzzle request failed.');
        setNeedsRefresh(true);
      }
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  };

  const refresh = async () => {
    if (!run || busy.current) return;
    busy.current = true;
    const request = generation.current;
    setPending(true); setError(null);
    try {
      const next = await ApiClient.getPuzzleRun(run.run_id);
      if (request !== generation.current) return;
      setRun(next); setNeedsRefresh(false);
      setNotice('Latest puzzle state loaded. Choose your next action.');
    } catch (failure) {
      if (request === generation.current) setError(failure instanceof Error ? failure.message : 'Could not refresh puzzle.');
    } finally {
      if (request === generation.current) { busy.current = false; setPending(false); }
    }
  };

  // Undo the parity of already played plies to keep the initial solver side on screen.
  const blackToMove = run?.fen.split(' ')[1] === 'b';
  const solverBlack = run ? blackToMove !== (run.played_moves.length % 2 === 1) : false;
  const active = run?.state === 'active';
  const latestHint = run?.hints.at(-1);
  return <section className="puzzles-screen" aria-labelledby="puzzles-title" aria-busy={pending}>
    <h1 id="puzzles-title">Puzzles</h1>
    <p>Practise the complete imported source continuation. The opponent’s setup move is already played.</p>
    <p className="muted">Lichess CC0 source-verified practice: the source line is legal, not an engine judgement of move quality. This does not award mastery or rating gains.</p>
    <div role="status" aria-live="polite">
      {pending ? <p>Loading puzzle state…</p> : null}
      {notice ? <p>{notice}</p> : null}
      {run ? <>
        <p>{Math.max(0, run.maximum_wrong_attempts - run.wrong_attempts)} wrong attempts remaining</p>
        {run.state === 'solved' ? <p>Puzzle solved. You completed the source continuation.</p> : null}
        {run.state === 'failed' ? <p>Puzzle failed. All wrong attempts have been used.</p> : null}
        {run.state === 'abandoned' ? <p>Puzzle abandoned. No success or failure was recorded.</p> : null}
      </> : null}
    </div>
    {error ? <div role="alert"><p>{error} Please retry loading the server state before continuing.</p>
      <button disabled={pending} onClick={() => void (run ? refresh() : start())}>Retry</button>
    </div> : null}
    {!run && catalog && catalog.status !== 'ready' ? <div className="study-panel">
      <p>{catalog.status === 'empty' ? 'No imported puzzles are available.' : 'You have exhausted the available puzzles.'}</p>
      <p>Ask the operator to import more puzzles into the application database:</p>
      <code>{IMPORT_COMMAND}</code>
      <p>Nothing downloads automatically.</p>
      <button disabled={pending} onClick={() => void start()}>Check for puzzles</button>
    </div> : null}
    {run ? <div className="puzzles-layout">
      <CoachBoard key={run.run_id} fen={run.fen} orientation={solverBlack ? 'black' : 'white'}
        disabled={pending || !active || needsRefresh} resetAfterAttempt testId="puzzle-board"
        onMove={(move) => mutate('move', move)} visualizations={latestHint?.visualizations} />
      <div className="study-panel">
        <h2>Source puzzle</h2>
        <p>Puzzle: {run.puzzle_id}</p>
        <p>Setup move (UCI): {run.setup}</p>
        <p>Played continuation (UCI): {run.played_moves.length ? run.played_moves.join(' ') : 'No solver moves yet.'}</p>
        <section aria-label="Puzzle hints">
          <button disabled={pending || !active || needsRefresh || run.hint_step >= 3} onClick={() => void mutate('hint')}>Show hint</button>
          <p>Hint {run.hint_step} of 3. The third hint can reveal the source line.</p>
          {run.hints.map(hint => <div key={hint.step}>
            <h3>Hint {hint.step}</h3>
            {hint.step !== 3 ? <p>{hint.text}</p> : <ol>{hint.candidates.map(candidate => <li key={candidate.move}>
              <strong>Source continuation: {candidate.san}</strong>
              <p>Source line (UCI): {candidate.line.join(' ')}</p>
              {candidate.reasons.length ? <ul>{candidate.reasons.map((reason, index) => <li key={index}><strong>{reason.tier === 'principle' ? 'Principle' : 'Tactical'}:</strong> {reason.description}</li>)}</ul> : <p>Source continuation; no engine-quality claim.</p>}
            </li>)}</ol>}
          </div>)}
        </section>
        <div className="puzzles-actions">
          {active ? <button disabled={pending || needsRefresh} onClick={() => void mutate('abandon')}>Abandon puzzle</button>
            : <button disabled={pending} onClick={() => void start()}>Next puzzle</button>}
        </div>
      </div>
    </div> : null}
  </section>;
}
