import { useEffect, useRef, useState, type ReactNode } from 'react';
import { flushSync } from 'react-dom';
import { Chess } from 'chess.js';
import { ApiClient, PuzzleConflictError } from '../api/client';
import type { PuzzleMoveExplanationRead, PuzzleReviewRead, PuzzleRunRead, Visualization } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { Button, Panel } from './Primitives';
import './PuzzleRound.css';

interface Attempt {
  move: string;
  before: string;
  after: string;
  san: string;
  reviewCursor?: number;
}
interface Replay {
  fen: string;
  pgn: string;
  ply: number;
  length: number;
  explanation?: PuzzleMoveExplanationRead;
}
interface Practice { fen: string; cursor: number; complete: boolean }

function replayLine(fen: string, moves: string[]): Replay {
  const game = new Chess(fen);
  for (const move of moves) game.move(move);
  return { fen, pgn: game.pgn(), ply: 0, length: moves.length };
}

export function PuzzleRound({ run, pending, needsRefresh, onMove, onRunChange, children }: {
  run: PuzzleRunRead;
  pending: boolean;
  needsRefresh: boolean;
  onMove: (move: string) => Promise<PuzzleRunRead | undefined>;
  onRunChange: (run: PuzzleRunRead) => void;
  children: ReactNode;
}) {
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [review, setReview] = useState<PuzzleReviewRead | null>(null);
  const [practice, setPractice] = useState<Practice | null>(null);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const alive = useRef(true);
  const working = useRef(false);
  const frame = useRef<HTMLDivElement>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const active = run.state === 'active';
  const terminal = run.state === 'solved' || run.state === 'failed';
  const solverBlack = (run.fen.split(' ')[1] === 'b') !== (run.played_moves.length % 2 === 1);
  const blocked = pending || loading || needsRefresh;

  const clearAttempt = () => {
    if (terminal && !practice) {
      void openReview(true);
      return;
    }
    setAttempt(null); setReplay(null); setError('');
    setMessage(practice ? 'Unscored retry. Try another move.' : 'Your turn. Try another move.');
    requestAnimationFrame(() => frame.current?.querySelector<HTMLInputElement>('input')?.focus());
  };

  const play = async (move: string): Promise<void> => {
    if (working.current || blocked || attempt || replay || practice?.complete) return;
    const before = practice?.fen ?? run.fen;
    const game = new Chess(before);
    const played = game.move(move);
    const candidate: Attempt = { move, before, after: game.fen(), san: played.san,
      ...(practice ? { reviewCursor: practice.cursor } : {}) };
    setError(''); setMessage('');
    if (practice && review) {
      if (move !== review.solution[practice.cursor] && !game.isCheckmate()) {
        flushSync(() => setAttempt(candidate));
        return;
      }
      let cursor = practice.cursor + 1;
      if (!game.isCheckmate() && cursor < review.solution.length) game.move(review.solution[cursor++]);
      const complete = game.isCheckmate() || cursor >= review.solution.length;
      flushSync(() => setPractice({ fen: game.fen(), cursor, complete }));
      setMessage(complete ? 'Unscored practice complete. Your recorded result has not changed.' : 'Source move accepted. Continue your unscored retry.');
      return;
    }
    if (!active) return;
    working.current = true;
    try {
      const next = await onMove(move);
      if (!alive.current || !next) return;
      if (next.wrong_attempts > run.wrong_attempts) {
        // Commit the held position before CoachBoard restores its controlled FEN.
        flushSync(() => setAttempt(candidate));
      }
    } finally { working.current = false; }
  };

  const openReview = async (retry: boolean) => {
    if (!terminal || working.current || blocked) return;
    working.current = true; setLoading(true); setError('');
    try {
      const result = review ?? await ApiClient.getPuzzleReview(run.run_id);
      if (!alive.current) return;
      const line = replayLine(result.initial_fen, result.solution);
      setReview(result); setAttempt(null);
      if (retry) {
        setReplay(null);
        setPractice({ fen: result.initial_fen, cursor: 0, complete: false });
        setMessage('Unscored retry. Unlimited attempts; no rating, streak or mastery changes.');
      } else {
        setPractice(null); setReplay(line); setMessage('Source solution review. Step through the moves on the board.');
      }
    } catch (failure) {
      if (alive.current) setError(failure instanceof Error ? failure.message : 'Could not load the solution.');
    } finally {
      working.current = false;
      if (alive.current) setLoading(false);
    }
  };

  const explain = async () => {
    if (!attempt || working.current || blocked) return;
    working.current = true; setLoading(true); setError('');
    try {
      const result = await ApiClient.explainPuzzleMove(run.run_id, {
        expected_cursor: run.cursor, expected_revision: run.revision, move: attempt.move,
        ...(attempt.reviewCursor !== undefined ? { review_cursor: attempt.reviewCursor } : {}),
      });
      if (!alive.current) return;
      const line = replayLine(result.fen_before, [result.move, ...result.reply_line]);
      onRunChange(result.run);
      setReplay({ ...line, ply: 1, explanation: result });
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof PuzzleConflictError) {
        onRunChange(failure.run); setAttempt(null); setReplay(null);
      }
      setError(failure instanceof Error ? failure.message : 'Could not analyze this move.');
    } finally {
      working.current = false;
      if (alive.current) setLoading(false);
    }
  };

  const showingAttempt = !!attempt && (!replay || replay.ply === 1);
  const annotations: Visualization[] | undefined = showingAttempt ? [
    { command: 'highlight_square', squares: [attempt.move.slice(0, 2), attempt.move.slice(2, 4)], description: `Your attempted move: ${attempt.san}` },
    ...(replay?.explanation?.visualizations.filter(item => item.command !== 'animate_line') ?? []),
  ] : replay || practice ? undefined : run.hints.at(-1)?.visualizations;

  return <div ref={frame} className={`board-workspace puzzle-round${showingAttempt ? ' puzzle-round--attempt' : ''}`} role="region" aria-label="Puzzle workspace">
    <div className="board-workspace__board">
      <CoachBoard fen={replay?.fen ?? attempt?.after ?? practice?.fen ?? run.fen}
        pgn={replay?.pgn} selectedPly={replay?.ply}
        onSelectedPlyChange={replay ? ply => setReplay({ ...replay, ply }) : undefined}
        orientation={solverBlack ? 'black' : 'white'}
        disabled={blocked || !!attempt || !!replay || (practice ? practice.complete : !active)}
        resetAfterAttempt testId="puzzle-board" onMove={play} visualizations={annotations} />
    </div>
    <Panel className="board-workspace__side working-controls" aria-label="Current puzzle">
      {run.state === 'solved' ? <section className="puzzle-result puzzle-result--solved" role="status">
        <span className="puzzle-result__mark" aria-hidden="true">✓</span>
        <div><h2>Puzzle solved</h2><p>Your scored round is complete. Your result is recorded.</p></div>
      </section> : run.state === 'failed' ? <section className="puzzle-result" role="status">
        <h2>Scored attempts complete</h2><p>Your result is recorded. Review the solution or keep practising without scoring.</p>
      </section> : null}
      {practice ? <section className="puzzle-feedback" aria-label="Unscored practice">
        <h3>{practice.complete ? 'Unscored practice complete' : 'Unscored retry'}</h3>
        <p>Unlimited attempts. Your rating, streak and mastery will not change.</p>
      </section> : null}
      {attempt ? <section className="puzzle-feedback puzzle-feedback--attempt" aria-label="Move feedback">
        <h3>{attempt.san}: not the source move</h3>
        <p>Your move stays on the board. A different move is not necessarily a bad move.</p>
        <div className="puzzles-actions">
          <Button disabled={blocked} onClick={clearAttempt}>Try again</Button>
          <Button disabled={blocked || !!replay?.explanation} onClick={() => void explain()}>Explain this move</Button>
        </div>
        {active ? <p>A full explanation reveals analysis and counts as assistance, like the final hint.</p> : null}
      </section> : null}
      {replay ? <section className="puzzle-feedback" aria-label={replay.explanation ? 'Move explanation' : 'Solution review'}>
        <h3>{replay.explanation ? ({ mistake: 'Verified mistake', alternative: 'Playable alternative', uncertain: 'No decisive verdict' }[replay.explanation.verdict]) : 'Source solution'}</h3>
        {replay.explanation ? <p>{replay.explanation.summary}</p> : null}
        <p>Position {replay.ply} of {replay.length}. {replay.ply === 0 ? 'Before the first move.' : replay.explanation && replay.ply === 1 ? 'Your attempted move.' : 'Continuation shown on the board.'}</p>
        <div className="puzzles-actions">
          <Button disabled={replay.ply === 0} onClick={() => setReplay({ ...replay, ply: replay.ply - 1 })}>Previous move</Button>
          <Button disabled={replay.ply === replay.length} onClick={() => setReplay({ ...replay, ply: replay.ply + 1 })}>Next move</Button>
          {!attempt ? <Button onClick={() => setReplay(null)}>Close solution</Button> : null}
        </div>
      </section> : null}
      {terminal ? <div className="puzzles-actions">
        <Button disabled={blocked} onClick={() => void openReview(true)}>{practice ? 'Restart unscored' : 'Retry unscored'}</Button>
        <Button disabled={blocked} onClick={() => void openReview(false)}>Review solution</Button>
      </div> : null}
      {loading ? <p role="status">Loading verified review…</p> : null}
      {error ? <p role="alert">{error}</p> : null}
      {message ? <p role="status">{message}</p> : null}
      {children}
    </Panel>
  </div>;
}
