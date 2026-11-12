import { useEffect, useRef, useState } from 'react';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL } from '../api/opponent';
import type { PlaySessionRead } from '../api/types';
import { OwnedGameReview } from './OwnedGameReview';

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
  const boardRef = useRef<HTMLDivElement>(null);
  const cg = useRef<Api | null>(null);
  const [chess] = useState(() => new Chess());
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fenInput, setFenInput] = useState(chess.fen());

  useEffect(() => {
    if (!boardRef.current) return;
    const api = Chessground(boardRef.current, {
      fen: chess.fen(),
      movable: { color: 'both', free: false, dests: getDests(chess) },
      events: { move: (orig, dest) => {
        try {
          chess.move({ from: orig, to: dest, promotion: 'q' });
          api.set({ fen: chess.fen(), movable: { dests: getDests(chess) } });
          setFenInput(chess.fen());
        } catch { api.set({ fen: chess.fen() }); }
      } },
    });
    cg.current = api;
    return () => { api.destroy(); cg.current = null; };
  }, [chess]);

  const handleLoadFen = () => {
    try {
      chess.load(fenInput);
      cg.current?.set({ fen: chess.fen(), movable: { dests: getDests(chess) } });
      setError(null);
    } catch { setError('Invalid FEN'); }
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
    {loading && <p>Loading...</p>}
    <div className="review-replay-layout">
      <div ref={boardRef} data-testid="free-analysis-board" style={{ width: 'min(400px, 100%)', aspectRatio: '1' }} />
      <div className="analysis-sidebar">
        <button type="button" onClick={playFromHere} disabled={loading || !onPlayFromHere} data-testid="play-from-here">{loading ? 'Starting...' : 'Play from here'}</button>
        <div className="fen-setup">
          <h3>FEN Setup</h3>
          <input type="text" aria-label="FEN" value={fenInput} onChange={event => setFenInput(event.target.value)} placeholder="Paste FEN here" style={{ width: '100%' }} />
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
          <button type="button" onClick={() => navigator.clipboard.writeText(chess.fen())}>Copy FEN</button>
        </div>
        <div className="multipv-container" data-testid="multipv-lines">
          <h3>Engine Evaluation</h3>
          <p>No engine analysis available for this position.</p>
        </div>
      </div>
    </div>
  </div>;
}

function getDests(chess: Chess): Map<Key, Key[]> {
  const dests = new Map<Key, Key[]>();
  for (const move of chess.moves({ verbose: true })) {
    const from = move.from as Key;
    const destinations = dests.get(from) ?? [];
    destinations.push(move.to as Key);
    dests.set(from, destinations);
  }
  return dests;
}
