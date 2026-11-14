import { useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL } from '../api/opponent';
import type { PlaySessionRead } from '../api/types';
import { OwnedGameReview } from './OwnedGameReview';
import { CoachBoard } from './CoachBoard';
import { resolveMove } from './moveEntry';
import { Button, Field, Panel } from './Primitives';
import './WorkingScreens.css';

interface AnalysisScreenProps {
  gameId?: string;
  initialPly?: number;
  onPlayFromHere?: (session: PlaySessionRead, fen: string) => void;
}

async function startPlayFromPosition(fen: string): Promise<PlaySessionRead> {
  const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
  const game = await ApiClient.createGame({
    pgn: `[FEN "${fen}"]\n[SetUp "1"]\n\n`,
    player_id: playerId,
    owner_color: fen.split(' ')[1] === 'b' ? 'black' : 'white',
  });
  return ApiClient.createPlaySession({
    player_id: playerId,
    game_id: game.id,
    opponent_config: { provider: 'stockfish', bot_level: DEFAULT_BOT_LEVEL },
  });
}

export function AnalysisScreen({ gameId, initialPly, onPlayFromHere }: AnalysisScreenProps) {
  return gameId
    ? <OwnedGameReview key={gameId} gameId={gameId} initialPly={initialPly} onPlayFromHere={onPlayFromHere} startPlayFromPosition={startPlayFromPosition} />
    : <FreeAnalysisBoard onPlayFromHere={onPlayFromHere} />;
}

function FreeAnalysisBoard({ onPlayFromHere }: Pick<AnalysisScreenProps, 'onPlayFromHere'>) {
  const fenField = useRef<HTMLInputElement>(null);
  const [chess] = useState(() => new Chess());
  const [fen, setFen] = useState(chess.fen());
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fenInput, setFenInput] = useState(chess.fen());

  const handleMove = (uci: string) => {
    const resolved = resolveMove(chess, uci);
    if (resolved.kind !== 'move') throw new Error('Could not apply analysis move.');
    const { from, to, promotion } = resolved.move;
    chess.move({ from, to, promotion });
    setFen(chess.fen());
    setFenInput(chess.fen());
    setError(null);
  };

  const handleLoadFen = () => {
    try {
      chess.load(fenInput);
      setFen(chess.fen());
      setError(null);
      setStatus('Position loaded.');
    } catch {
      setError('Invalid FEN');
      fenField.current?.focus();
    }
  };

  const playFromHere = async () => {
    if (!onPlayFromHere) return;
    setLoading(true);
    setError(null);
    try {
      const fen = chess.fen();
      onPlayFromHere(await startPlayFromPosition(fen), fen);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Failed to start game');
    } finally { setLoading(false); }
  };

  return <div className="analysis-screen working-screen">
    <h2>Analysis Board</h2>
    {error && <p role="alert">{error}</p>}
    <p role="status">{loading ? 'Loading...' : status}</p>
    <div className="board-workspace">
      <div className="board-workspace__board">
        <CoachBoard fen={fen} pgn={chess.pgn()} onMove={handleMove} disabled={loading} testId="free-analysis-board" />
      </div>
      <Panel className="board-workspace__side working-controls" aria-label="Analysis controls">
        <Button variant="primary" onClick={playFromHere} disabled={loading || !onPlayFromHere} data-testid="play-from-here">{loading ? 'Starting...' : 'Play from here'}</Button>
        <section className="working-controls__section">
          <h3>FEN setup</h3>
          <Field ref={fenField} label="FEN" id="analysis-fen" type="text" value={fenInput} onChange={event => setFenInput(event.target.value)} placeholder="Paste FEN here" />
          <Button onClick={handleLoadFen}>Load FEN</Button>
        </section>
        <section className="working-controls__section">
          <h3>Export</h3>
          <div className="working-actions">
            <Button onClick={() => {
              const url = URL.createObjectURL(new Blob([chess.pgn()], { type: 'text/plain' }));
              const link = document.createElement('a');
              link.href = url;
              link.download = 'analysis.pgn';
              link.click();
              URL.revokeObjectURL(url);
            }}>Download PGN</Button>
            <Button onClick={() => {
              void navigator.clipboard.writeText(chess.fen())
                .then(() => setStatus('FEN copied.'))
                .catch(() => setError('Could not copy FEN.'));
            }}>Copy FEN</Button>
          </div>
        </section>
        <section className="working-controls__section" data-testid="multipv-lines">
          <h3>Engine evaluation</h3>
          <p>No engine analysis available for this position.</p>
        </section>
      </Panel>
    </div>
  </div>;
}

