import { useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL } from '../api/opponent';
import type { FamousGameDecision, FamousGameRead, PlaySessionRead } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { resolveMove } from './moveEntry';

export interface FamousGameStudyScreenProps {
  onPlayFromHere: (session: PlaySessionRead, fen: string) => void;
}


export function FamousGameStudyScreen({ onPlayFromHere }: FamousGameStudyScreenProps) {
  const [games, setGames] = useState<FamousGameRead[]>([]);
  const [selectedGame, setSelectedGame] = useState<FamousGameRead | null>(null);
  const [decisionIndex, setDecisionIndex] = useState(0);
  const [playerId, setPlayerId] = useState('');
  const [prediction, setPrediction] = useState('');
  const [hintsUsed, setHintsUsed] = useState(0);
  const [currentFen, setCurrentFen] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [authorizationError, setAuthorizationError] = useState('');
  const [feedback, setFeedback] = useState('');
  const [comparison, setComparison] = useState('');
  const predictionField = useRef<HTMLInputElement>(null);
  const studyHeading = useRef<HTMLHeadingElement>(null);
  const catalogueHeading = useRef<HTMLHeadingElement>(null);
  const busy = useRef(false);
  const [predictionError, setPredictionError] = useState('');

  const currentDecision = selectedGame?.payload.decisions[decisionIndex] ?? null;
  const studyComplete = selectedGame !== null && currentDecision === null;

  useEffect(() => {
    let active = true;

    void ApiClient.getFamousGames()
      .then((loadedGames) => {
        if (!active) return;
        setGames(loadedGames);
      })
      .catch((caughtError: unknown) => {
        if (!active) return;
        setError(
          caughtError instanceof Error ? caughtError.message : 'Failed to load famous games',
        );
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    void ensurePlayerAuthorization(getOrCreatePlayerId())
      .then((authorizedPlayerId) => {
        if (active) setPlayerId(authorizedPlayerId);
      })
      .catch((caughtError: unknown) => {
        if (!active) return;
        setAuthorizationError(
          caughtError instanceof Error ? caughtError.message : 'Failed to authorize player',
        );
      });

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (selectedGame) studyHeading.current?.focus();
    else if (!loading) catalogueHeading.current?.focus();
  }, [selectedGame, decisionIndex, loading]);

  const selectGame = (game: FamousGameRead) => {
    const firstDecision = game.payload.decisions[0];
    if (!firstDecision) {
      setError('This study has no annotated decisions.');
      return;
    }

    setSelectedGame(game);
    setDecisionIndex(0);
    setCurrentFen(firstDecision.fen);
    setPrediction('');
    setHintsUsed(0);
    setPredictionError('');
    setFeedback('');
    setComparison('');
    setError('');
  };

  const showDecision = (decision: FamousGameDecision) => {
    setCurrentFen(decision.fen);
    setPrediction('');
    setHintsUsed(0);
    setFeedback('');
    setComparison('');
    setPredictionError('');
    setError('');
  };

  const advanceDecision = () => {
    if (!selectedGame) return;

    const nextDecisionIndex = decisionIndex + 1;
    setDecisionIndex(nextDecisionIndex);
    const nextDecision = selectedGame.payload.decisions[nextDecisionIndex];
    if (nextDecision) showDecision(nextDecision);
  };

  const submitPrediction = async () => {
    if (!selectedGame || !currentDecision || !playerId || !prediction.trim() || busy.current) return;

    const resolved = resolveMove(new Chess(currentDecision.fen), prediction);
    if (resolved.kind !== 'move') {
      setPredictionError(resolved.kind === 'promotion'
        ? 'Choose a promotion piece: add q, r, b or n to your UCI move, or use canonical SAN such as e8=Q.'
        : resolved.message);
      predictionField.current?.focus();
      return;
    }

    const move = resolved.move.san;
    busy.current = true;
    setPredictionError('');
    setSubmitting(true);
    setError('');

    try {
      const attempt = await ApiClient.recordFamousGameAttempt(selectedGame.id, {
        player_id: playerId,
        decision_id: currentDecision.id,
        hint_assisted: hintsUsed > 0,
        response_payload: { move },
      });
      setFeedback(attempt.success ? 'Correct.' : `Played move: ${currentDecision.played_move}`);
      setComparison(currentDecision.comparison);
    } catch (caughtError: unknown) {
      setError(
        caughtError instanceof Error ? caughtError.message : 'Failed to record your prediction',
      );
    } finally {
      setSubmitting(false);
      busy.current = false;
    }
  };

  const showHint = () => {
    if (!currentDecision) return;

    const hint = currentDecision.hints[hintsUsed];
    if (!hint) {
      setFeedback('No further hints are available.');
      return;
    }
    setHintsUsed((used) => used + 1);
    setFeedback(hint);
  };

  const showMove = () => {
    if (!currentDecision) return;

    const chess = new Chess(currentDecision.fen);
    chess.move(currentDecision.played_move);
    setCurrentFen(chess.fen());
    setFeedback(`Played move: ${currentDecision.played_move}`);
    setComparison(currentDecision.comparison);
  };

  const continueFromHere = async () => {
    if (!currentDecision || !playerId || busy.current) return;

    busy.current = true;

    setSubmitting(true);
    setError('');
    try {
      const session = await ApiClient.createPlaySession({
        player_id: playerId,
        opponent_config: { provider: 'stockfish', bot_level: DEFAULT_BOT_LEVEL },
        initial_fen: currentDecision.fen,
      });
      onPlayFromHere(session, currentDecision.fen);
    } catch (caughtError: unknown) {
      setError(
        caughtError instanceof Error ? caughtError.message : 'Failed to start a play session',
      );
    } finally {
      setSubmitting(false);
      busy.current = false;
    }
  };

  if (loading) return <div role="status">Loading famous games...</div>;
  if (error && !selectedGame) return <div role="alert">{error}</div>;

  if (!selectedGame) {
    return (
      <section className="famous-games-list" data-testid="famous-games-catalogue">
        <h2 ref={catalogueHeading} tabIndex={-1}>Study Famous Games</h2>
        {authorizationError && <div role="alert">Predictions and engine play unavailable: {authorizationError}</div>}
        <ul>
          {games.map((game) => (
            <li key={game.id}>
              <button type="button" onClick={() => selectGame(game)}>
                {game.payload.title}
              </button>
            </li>
          ))}
        </ul>
      </section>
    );
  }

  return (
    <section className="famous-game-study" data-testid="famous-game-study">
      <CoachBoard fen={currentFen} disabled testId="famous-game-board" />
      <div className="controls-section">
        <h2 ref={studyHeading} tabIndex={-1}>{selectedGame.payload.title}</h2>
        <p>{selectedGame.payload.historical_context}</p>
        <p>{selectedGame.payload.strategic_context}</p>
        {error && <div role="alert">{error}</div>}
        {authorizationError && <div role="alert">Predictions and engine play unavailable: {authorizationError}</div>}
        <p role="status">{submitting ? 'Submitting...' : studyComplete ? 'Game Completed' : feedback}</p>
        {comparison && <p data-testid="move-comparison">{comparison}</p>}

        {currentDecision && (
          <form className="predict-controls" onSubmit={(event) => {
            event.preventDefault();
            void submitPrediction();
          }}>
            <h3>Predict the next move</h3>
            <p>{currentDecision.prompt}</p>
            <label htmlFor="predict-input">Your move</label>
            <input
              id="predict-input"
              ref={predictionField}
              aria-describedby={predictionError ? 'prediction-help prediction-error' : 'prediction-help'}
              aria-invalid={predictionError ? true : undefined}
              readOnly={submitting}
              autoComplete="off"
              autoCapitalize="none"
              spellCheck={false}
              value={prediction}
              onChange={(event) => {
                setPrediction(event.currentTarget.value);
                setPredictionError('');
              }}
              placeholder="e.g. e2e4 or Nf3"
            />
            <p id="prediction-help">Enter strict UCI (e2e4, e7e8q) or canonical English SAN (Nf3, O-O). Include the promotion piece.</p>
            {predictionError && <p id="prediction-error" role="alert">{predictionError}</p>}
            <button type="submit" disabled={!playerId || submitting || !prediction.trim()}>
              Submit
            </button>
            <button type="button" disabled={submitting} onClick={showHint}>Show Hint</button>
            <button type="button" disabled={submitting} onClick={showMove}>Show Move</button>
            <button type="button" disabled={!playerId || submitting} onClick={() => void continueFromHere()}>
              Continue from here against engine
            </button>
            <button type="button" disabled={submitting} onClick={advanceDecision}>
              {decisionIndex + 1 < selectedGame.payload.decisions.length ? 'Next decision' : 'Complete study'}
            </button>
          </form>
        )}

        {studyComplete && <h3>Game Completed</h3>}
        <button type="button" disabled={submitting} onClick={() => setSelectedGame(null)}>Back to Games</button>
      </div>
    </section>
  );
}
