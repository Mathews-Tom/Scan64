import { useRef, useState } from 'react';
import { Chess } from 'chess.js';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL } from '../api/opponent';
import type { PlaySessionRead } from '../api/types';
import { OwnedGameReview } from './OwnedGameReview';
import { CoachBoard } from './CoachBoard';
import { resolveMove } from './moveEntry';

interface AnalysisScreenProps {
  gameId?: string;
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

export function AnalysisScreen({ gameId, onPlayFromHere }: AnalysisScreenProps) {
  return gameId
    ? <OwnedGameReview key={gameId} gameId={gameId} onPlayFromHere={onPlayFromHere} startPlayFromPosition={startPlayFromPosition} />
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

  return <div className="analysis-screen">
    <h2>Analysis Board</h2>
    {error && <p role="alert">{error}</p>}
    <p role="status">{loading ? 'Loading...' : status}</p>
    <div className="review-replay-layout">
      <CoachBoard fen={fen} pgn={chess.pgn()} onMove={handleMove} disabled={loading} testId="free-analysis-board" />
      <div className="analysis-sidebar">
        <button type="button" onClick={playFromHere} disabled={loading || !onPlayFromHere} data-testid="play-from-here">{loading ? 'Starting...' : 'Play from here'}</button>
        <div className="fen-setup">
          <h3>FEN Setup</h3>
          <label htmlFor="analysis-fen">FEN</label>
          <input ref={fenField} id="analysis-fen" type="text" value={fenInput} onChange={event => setFenInput(event.target.value)} placeholder="Paste FEN here" style={{ width: '100%' }} />
          <button type="button" onClick={handleLoadFen}>Load FEN</button>
        </div>
        <div className="pgn-export">
          <h3>Export</h3>
          <button type="button" onClick={() => {
            const url = URL.createObjectURL(new Blob([chess.pgn()], { type: 'text/plain' }));
            const link = document.createElement('a');
            link.href = url;
            link.download = 'analysis.pgn';
            link.click();
            URL.revokeObjectURL(url);
          }}>Download PGN</button>
          <button type="button" onClick={() => {
            void navigator.clipboard.writeText(chess.fen())
              .then(() => setStatus('FEN copied.'))
              .catch(() => setError('Could not copy FEN.'));
          }}>Copy FEN</button>
        </div>
        <div className="multipv-container" data-testid="multipv-lines">
          <h3>Engine Evaluation</h3>
          <p>No engine analysis available for this position.</p>
        </div>
      </div>
    </div>
  </div>;
}

