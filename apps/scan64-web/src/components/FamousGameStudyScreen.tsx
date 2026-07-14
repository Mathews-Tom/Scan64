import { useEffect, useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import { ApiClient, getOrCreatePlayerId } from '../api/client';
import type { FamousGameDecision, FamousGameRead, PlaySessionRead } from '../api/types';

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
  const [feedback, setFeedback] = useState('');
  const [comparison, setComparison] = useState('');
  const boardRef = useRef<HTMLDivElement>(null);
  const chessgroundRef = useRef<Api | null>(null);

  const currentDecision = selectedGame?.payload.decisions[decisionIndex] ?? null;
  const studyComplete = selectedGame !== null && currentDecision === null;

  useEffect(() => {
    setPlayerId(getOrCreatePlayerId());
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

    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedGame || !boardRef.current) return;

    chessgroundRef.current?.destroy();
    const board = Chessground(boardRef.current, {
      fen: selectedGame.payload.decisions[0]?.fen ?? '',
      movable: { color: undefined, free: false, dests: new Map() },
    });
    chessgroundRef.current = board;

    return () => {
      board.destroy();
      if (chessgroundRef.current === board) chessgroundRef.current = null;
    };
  }, [selectedGame]);

  useEffect(() => {
    chessgroundRef.current?.set({ fen: currentFen });
  }, [currentFen]);

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
  };

  const advanceDecision = () => {
    if (!selectedGame) return;

    const nextDecisionIndex = decisionIndex + 1;
    setDecisionIndex(nextDecisionIndex);
    const nextDecision = selectedGame.payload.decisions[nextDecisionIndex];
    if (nextDecision) showDecision(nextDecision);
  };

  const submitPrediction = async () => {
    if (!selectedGame || !currentDecision || !playerId || !prediction.trim()) return;

    const move = prediction.trim();
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
    if (!currentDecision || !playerId) return;

    setSubmitting(true);
    setError('');
    try {
      const session = await ApiClient.createPlaySession({
        player_id: playerId,
        opponent_config: { strength: '1' },
        initial_fen: currentDecision.fen,
      });
      onPlayFromHere(session, currentDecision.fen);
    } catch (caughtError: unknown) {
      setError(
        caughtError instanceof Error ? caughtError.message : 'Failed to start a play session',
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) return <div>Loading famous games...</div>;
  if (error && !selectedGame) return <div role="alert">{error}</div>;

  if (!selectedGame) {
    return (
      <section className="famous-games-list" data-testid="famous-games-catalogue">
        <h2>Study Famous Games</h2>
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
      <div ref={boardRef} aria-label="Famous game board" style={{ height: '400px', width: '400px' }} />
      <div className="controls-section">
        <h2>{selectedGame.payload.title}</h2>
        <p>{selectedGame.payload.historical_context}</p>
        <p>{selectedGame.payload.strategic_context}</p>
        {error && <div role="alert">{error}</div>}
        {feedback && <p role="status">{feedback}</p>}
        {comparison && <p data-testid="move-comparison">{comparison}</p>}

        {currentDecision && (
          <div className="predict-controls">
            <h3>Predict the next move</h3>
            <p>{currentDecision.prompt}</p>
            <label htmlFor="predict-input">Your move</label>
            <input
              id="predict-input"
              value={prediction}
              onChange={(event) => setPrediction(event.currentTarget.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void submitPrediction();
              }}
              placeholder="e.g. Nf3"
            />
            <button type="button" disabled={submitting || !prediction.trim()} onClick={() => void submitPrediction()}>
              Submit
            </button>
            <button type="button" onClick={showHint}>Show Hint</button>
            <button type="button" onClick={showMove}>Show Move</button>
            <button type="button" disabled={submitting} onClick={() => void continueFromHere()}>
              Continue from here against engine
            </button>
            <button type="button" onClick={advanceDecision}>
              {decisionIndex + 1 < selectedGame.payload.decisions.length ? 'Next decision' : 'Complete study'}
            </button>
          </div>
        )}

        {studyComplete && <h3>Game Completed</h3>}
        <button type="button" onClick={() => setSelectedGame(null)}>Back to Games</button>
      </div>
    </section>
  );
}
