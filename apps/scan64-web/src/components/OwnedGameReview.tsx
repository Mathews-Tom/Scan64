import { useEffect, useMemo, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient, ApiRequestError } from '../api/client';
import type { GameAnalysisStatusRead, GameReviewRead, PlaySessionRead, ReviewBand } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { CriticalMomentReview } from './CriticalMomentReview';
import { ReviewProbabilityGraph } from './ReviewProbabilityGraph';
import './OwnedGameReview.css';

interface Props {
  gameId: string;
  onPlayFromHere?: (session: PlaySessionRead, fen: string) => void;
  startPlayFromPosition: (fen: string) => Promise<PlaySessionRead>;
}

const BAND_LABELS: Record<ReviewBand, string> = {
  sound: 'Sound', inaccuracy: 'Inaccuracy', mistake: 'Mistake', blunder: 'Blunder', unscored: 'Unscored',
};

async function readReviewState(gameId: string) {
  const [review, status] = await Promise.allSettled([
    ApiClient.getGameReview(gameId), ApiClient.getGameAnalysisStatus(gameId),
  ]);
  let error: string | null = null;
  for (const result of [review, status]) {
    if (result.status === 'fulfilled') continue;
    const cause: unknown = result.reason;
    error = cause instanceof ApiRequestError && cause.status === 404
      ? 'Game analysis was not found.'
      : cause instanceof Error ? cause.message : 'Failed to load game review.';
    break;
  }
  return {
    review: review.status === 'fulfilled' ? review.value : null,
    status: status.status === 'fulfilled' ? status.value : null,
    error,
  };
}

export function OwnedGameReview({ gameId, onPlayFromHere, startPlayFromPosition }: Props) {
  const [review, setReview] = useState<GameReviewRead | null>(null);
  const [status, setStatus] = useState<GameAnalysisStatusRead | null>(null);
  const [selectedPly, setSelectedPly] = useState(0);
  const [retryLessonId, setRetryLessonId] = useState<string | null>(null);
  const [openedRetries, setOpenedRetries] = useState<string[]>([]);
  const [busy, setBusy] = useState<'load' | 'refresh' | 'analysis' | 'backfill' | 'play' | null>('load');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let ignore = false;
    void readReviewState(gameId).then(data => {
      if (ignore) return;
      setReview(data.review);
      setStatus(data.status);
      setError(data.error);
      setBusy(null);
    });
    return () => { ignore = true; };
  }, [gameId]);

  const initialFen = review?.initial_fen;
  const moves = review?.moves;
  const pgn = useMemo(() => {
    if (initialFen === undefined || moves === undefined) return '';
    const chess = new Chess(initialFen);
    for (const move of moves) chess.move(move.move_uci);
    return chess.pgn();
  }, [initialFen, moves]);

  const refresh = async () => {
    setBusy('refresh');
    setError(null);
    const data = await readReviewState(gameId);
    setReview(data.review);
    setStatus(data.status);
    setError(data.error);
    setBusy(null);
  };

  const startAnalysis = async () => {
    setBusy('analysis');
    setError(null);
    try {
      const job = await ApiClient.createAnalysisJob(gameId);
      setStatus({ status: job.status });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to start analysis.');
    } finally { setBusy(null); }
  };

  const backfill = async () => {
    setBusy('backfill');
    setError(null);
    try {
      const data = await ApiClient.backfillGameReview(gameId);
      setReview(data);
      // Backfill writes no learning state. Only GET may restore the owned retry session.
      setReview(await ApiClient.getGameReview(gameId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to backfill review.');
    } finally { setBusy(null); }
  };

  const selectedMove = review?.moves.find(move => move.ply === selectedPly);
  const selectedFen = selectedMove?.fen ?? review?.initial_fen;
  const playFromHere = async () => {
    if (!onPlayFromHere || selectedFen === undefined) return;
    setBusy('play');
    setError(null);
    try { onPlayFromHere(await startPlayFromPosition(selectedFen), selectedFen); }
    catch (caught) { setError(caught instanceof Error ? caught.message : 'Failed to start game.'); }
    finally { setBusy(null); }
  };

  const lastPly = review?.moves.at(-1)?.ply ?? 0;
  return <div className="analysis-screen owned-game-review">
    <h2>Analysis Board</h2>
    {error && <p role="alert">{error}</p>}
    {busy === 'load' && <p>Loading...</p>}
    {busy !== 'load' && <button type="button" onClick={refresh} disabled={busy !== null}>Refresh review</button>}
    {status?.status === 'not_analysed' && <>
      <p data-testid="analysis-not-analysed">This game has not been analysed yet.</p>
      {(review === null || review.owner_color !== null) && <button type="button" data-testid="start-analysis" onClick={startAnalysis} disabled={busy !== null}>Analyse game</button>}
    </>}
    {(status?.status === 'pending' || status?.status === 'running') && <p data-testid="analysis-in-progress">Analysis is in progress. Refresh review when it completes.</p>}
    {status?.status === 'failed' && <>
      <p data-testid="analysis-failed">Analysis failed.</p>
      <button type="button" data-testid="retry-analysis" onClick={startAnalysis} disabled={busy !== null}>Retry analysis</button>
    </>}
    {review && <>
      {review.owner_color === null ? <p className="review-notice" data-testid="review-unknown-owner">
        Your side is unknown for this game. Replay and White-perspective scores remain available; owner bands, accuracy and learning attribution are unavailable. Scan64 does not infer or repair your colour.
      </p> : <p>You played {review.owner_color === 'white' ? 'White' : 'Black'}. Bands and accuracy include only your moves.</p>}
      {review.backfill_required && status?.status === 'completed' && <section className="review-notice" aria-label="Missing move review">
        <p>This older analysis is missing per-move scores. Every move can still be replayed.</p>
        <p>Review-only backfill runs one 10,000-node fast pass. It adds review scores and their position/analysis provenance, not lessons, evidence, diagnoses or profile updates.</p>
        <button type="button" data-testid="backfill-review" onClick={backfill} disabled={busy !== null}>
          {busy === 'backfill' ? 'Backfilling review...' : 'Backfill review scores'}
        </button>
      </section>}
      <section className="review-summary" aria-label="Owner move summary">
        <div><h3>Scan64 accuracy</h3><p data-testid="review-accuracy">{review.owner_color === null || review.accuracy === null
          ? 'Unavailable' : `${review.accuracy.toFixed(1)}%`}</p></div>
        <div><h3>Coverage</h3><p data-testid="review-coverage">{review.owner_color === null ? 'Unavailable: your side is unknown'
          : `${review.scored_moves} / ${review.total_owner_moves} owner moves${review.total_owner_moves > 0 ? ` (${(100 * review.scored_moves / review.total_owner_moves).toFixed(1)}%)` : ''}`}</p></div>
        <dl className="review-band-counts" data-testid="review-band-counts">
          {Object.entries(BAND_LABELS).map(([band, label]) => <div key={band} data-band={band}>
            <dt>{label}</dt><dd>{review.owner_color === null ? '—' : review.band_counts[band as ReviewBand]}</dd>
          </div>)}
        </dl>
      </section>
      <p className="muted">Coarse 10,000-node fast-pass estimates, not Elo or Lichess accuracy. Missing scores are unscored, never zero loss. Accuracy excludes opponent moves and unscored owner moves; read it alongside coverage and all five band counts.</p>
      <section aria-label="Good move credit">
        <h3>Good move credit</h3>
        <p>Only supported tactical best moves earn skill credit, at most two per skill per game. Credits favor the strongest proofs; other qualifying moves show “per-game credit cap reached”. “No in-app hint” describes Scan64's records, not independence from external assistance.</p>
        {!review.moves.some(move => move.is_owner_move && move.credit) && <p>No credited moves in this review.</p>}
        <ul>{review.moves.filter(move => move.is_owner_move && move.credit).map(move => <li key={move.ply}>
          <button type="button" aria-current={selectedPly === move.ply ? 'step' : undefined}
            onClick={() => setSelectedPly(move.ply)}>Credited move at ply {move.ply}: {move.san}</button>
          <p>No in-app hint · {typeof move.credit?.skill_id === 'string'
            ? move.credit.skill_id.replaceAll('_', ' ').replaceAll('.', ' / ') : 'Tactical skill'}</p>
          <p>{typeof move.credit?.explanation === 'string' ? move.credit.explanation : 'Stored tactical proof is available for this credited move.'}</p>
        </li>)}</ul>
      </section>
      <ReviewProbabilityGraph review={review} selectedPly={selectedPly} onSelectedPlyChange={setSelectedPly} />
      <div className="review-replay-layout">
        <section className="owned-review-board" aria-label="Selected game position">
          <CoachBoard fen={review.initial_fen} pgn={pgn} selectedPly={selectedPly}
            onSelectedPlyChange={setSelectedPly} orientation={review.owner_color ?? 'white'} disabled testId="review-board" />
          <p aria-live="polite" data-testid="review-selected-position">{selectedPly === 0 ? 'Initial position'
            : `Ply ${selectedPly}: ${selectedMove?.san}${selectedPly === lastPly ? ' (final position)' : ''}`}</p>
          <nav className="review-controls" aria-label="Replay controls">
            <button type="button" onClick={() => setSelectedPly(0)} disabled={selectedPly === 0}>First</button>
            <button type="button" onClick={() => setSelectedPly(ply => ply - 1)} disabled={selectedPly === 0}>Previous</button>
            <button type="button" onClick={() => setSelectedPly(ply => ply + 1)} disabled={selectedPly === lastPly}>Next</button>
            <button type="button" onClick={() => setSelectedPly(lastPly)} disabled={selectedPly === lastPly}>Last</button>
          </nav>
          <div className="review-controls">
            <button type="button" data-testid="play-from-here" disabled={busy !== null || !onPlayFromHere} onClick={playFromHere}>{busy === 'play' ? 'Starting...' : 'Play from here'}</button>
            <button type="button" onClick={() => navigator.clipboard.writeText(selectedFen!)}>Copy FEN</button>
            <button type="button" onClick={() => {
              const url = URL.createObjectURL(new Blob([pgn], { type: 'text/plain' }));
              const link = document.createElement('a');
              link.href = url;
              link.download = 'analysis.pgn';
              link.click();
              URL.revokeObjectURL(url);
            }}>Download PGN</button>
          </div>
        </section>
        <section aria-label="Every game move" className="review-moves">
          <h3>Move by move</h3>
          <p className="muted">Select any move, not just a lesson. ★ marks a trusted key moment.</p>
          <button type="button" aria-current={selectedPly === 0 ? 'step' : undefined} onClick={() => setSelectedPly(0)}>Initial position</button>
          <ol data-testid="review-move-list">
            {review.moves.map(move => <li key={move.ply}>
              <button type="button" data-testid={`review-move-${move.ply}`} data-band={move.is_owner_move ? move.band ?? 'unscored' : undefined}
                aria-current={selectedPly === move.ply ? 'step' : undefined} onClick={() => setSelectedPly(move.ply)}>
                <span>{move.source_fen.split(' ')[5]}{move.mover === 'white' ? '.' : '...'} {move.san}</span>
                <span>{review.owner_color === null ? `${move.mover === 'white' ? 'White' : 'Black'} · owner unknown`
                  : move.is_owner_move ? `Your move · ${BAND_LABELS[move.band ?? 'unscored']}` : 'Opponent move'}</span>
                {review.key_moments.some(moment => moment.ply === move.ply) && <span aria-label="Key moment">★</span>}
              </button>
              {move.is_owner_move && <p data-testid={`review-credit-${move.ply}`}>
                {move.credit ? 'Skill credit · no in-app hint'
                  : `No skill credit: ${move.credit_exclusion ?? 'credit history unavailable'}`}
              </p>}
            </li>)}
          </ol>
        </section>
      </div>
      <section className="review-key-moments" aria-label="Trusted key moments">
        <h3>Key moments</h3>
        <p className="muted">Up to three stored, reverified lessons explain one trusted cause each. Retry starts before the selected move and shares Daily Training's lifetime attempt cap and hinted half credit.</p>
        {review.key_moments.length === 0 && <p>No trusted key moments are available. A scored mistake does not always have a verified lesson.</p>}
        {review.key_moments.map(moment => <article key={moment.lesson_id} data-testid={`review-key-moment-${moment.ply}`}>
          <button type="button" aria-current={selectedPly === moment.ply ? 'step' : undefined} onClick={() => setSelectedPly(moment.ply)}>Key moment at ply {moment.ply}</button>
          {moment.lesson.diagnosis && <h4>{moment.lesson.diagnosis.primary.replaceAll('_', ' ').replaceAll('.', ' / ')}</h4>}
          {moment.lesson.explanation && <p>{moment.lesson.explanation.text}</p>}
          <p>{moment.attempts_used} lifetime attempts used; {moment.attempts_remaining} remaining.</p>
          {moment.attempts_remaining === 0 ? <>
            <p>Attempts exhausted. This moment is available for study, not another retry.</p>
            <p>Accepted moves: {moment.lesson.interaction.accepted_moves.map(move => move.san).join(', ')}</p>
          </> : review.session_id === null ? <p>Retry is unavailable without an owned study session.</p> :
            <button type="button" onClick={() => {
              setSelectedPly(moment.ply);
              setRetryLessonId(retryLessonId === moment.lesson_id ? null : moment.lesson_id);
              setOpenedRetries(ids => ids.includes(moment.lesson_id) ? ids : [...ids, moment.lesson_id]);
            }}>{retryLessonId === moment.lesson_id ? 'Close retry' : 'Retry this moment'}</button>}
          {openedRetries.includes(moment.lesson_id) && review.session_id !== null && <div hidden={retryLessonId !== moment.lesson_id}>
            <CriticalMomentReview key={moment.lesson_id}
              lesson={moment.lesson} opportunityId={moment.lesson_id} sessionId={review.session_id}
              initialAttemptsUsed={moment.attempts_used} initialAttemptsRemaining={moment.attempts_remaining}
              onAttemptRecorded={attemptsUsed => setReview(current => current === null ? null : {
                ...current, key_moments: current.key_moments.map(item => item.lesson_id !== moment.lesson_id ? item : {
                  ...item, attempts_remaining: Math.max(0, item.attempts_remaining - (attemptsUsed - item.attempts_used)), attempts_used: attemptsUsed,
                }),
              })}
              onComplete={() => setRetryLessonId(null)} />
          </div>}
        </article>)}
      </section>
    </>}
  </div>;
}
