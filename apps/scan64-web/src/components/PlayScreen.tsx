import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import { PlaySession } from '../api/types';
import { Chessground } from 'chessground';
import { Api } from 'chessground/api';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';

export function PlayScreen() {
  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  const [session, setSession] = useState<PlaySession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const chessRef = useRef(new Chess());

  useEffect(() => {
    if (boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        movable: {
          color: 'white',
          free: false,
        },
      });
      setCg(api);
    }
  }, [cg]);

  const startGame = async () => {
    try {
      setError(null);
      const newSession = await ApiClient.createPlaySession('1500');
      setSession(newSession);
      chessRef.current.load(newSession.fen);
      if (cg) {
        cg.set({
          fen: newSession.fen,
          movable: {
            color: 'white',
            dests: getDests(chessRef.current),
            events: {
              after: handleMove,
            },
          },
        });
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    }
  };

  const getDests = (chess: Chess) => {
    const dests = new Map<string, string[]>();
    chess.moves({ verbose: true }).forEach((m) => {
      const from = m.from;
      const to = m.to;
      const d = dests.get(from) || [];
      d.push(to);
      dests.set(from, d);
    });
    return dests;
  };

  const handleMove = async (orig: string, dest: string) => {
    if (!session || !cg) return;
    try {
      const lan = `${orig}${dest}`;
      // Note: simplistic move applying. Real implementation needs promotion handling, 
      // but for "minimal production-shaped" we assume standard moves.
      chessRef.current.move({ from: orig, to: dest, promotion: 'q' });
      cg.set({ fen: chessRef.current.fen(), movable: { color: undefined } }); // Lock board while waiting

      const updatedSession = await ApiClient.makePlaySessionMove(session.id, { lan });
      setSession(updatedSession);
      chessRef.current.load(updatedSession.fen);
      
      cg.set({
        fen: updatedSession.fen,
        movable: {
          color: 'white',
          dests: getDests(chessRef.current),
        },
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      // Revert board to last known good state on error
      cg.set({ fen: chessRef.current.fen() });
    }
  };

  return (
    <div className="play-screen" data-testid="play-screen">
      <h1>Play against Scan64</h1>
      <button onClick={startGame} data-testid="start-btn">Start Game</button>
      {error && <div className="error">{error}</div>}
      <div 
        ref={boardRef} 
        style={{ width: '400px', height: '400px' }} 
        data-testid="chessground-board" 
      />
      {session && (
        <div data-testid="session-info">
          Status: {session.status}
        </div>
      )}
    </div>
  );
}
