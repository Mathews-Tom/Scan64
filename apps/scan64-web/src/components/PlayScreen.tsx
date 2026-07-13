import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import type { PlaySessionRead } from '../api/types';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';

export function PlayScreen() {
  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  const [session, setSession] = useState<PlaySessionRead | null>(null);
  const sessionRef = useRef<PlaySessionRead | null>(null);
  const [playerId, setPlayerId] = useState('');
  const [playerName, setPlayerName] = useState('');
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
      let pid = playerId;
      if (!pid) {
        pid = 'player-' + Date.now();
        setPlayerId(pid);
      }
      await ApiClient.createPlayer({ id: pid, display_name: playerName || 'Anonymous' });
      
      const newSession = await ApiClient.createPlaySession({ 
        player_id: pid, 
        opponent_config: { strength: '1500' } 
      });
      sessionRef.current = newSession;
      setSession(newSession);
      chessRef.current.reset();
      if (cg) {
        cg.set({
          fen: chessRef.current.fen(),
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
    const dests = new Map<Key, Key[]>();
    chess.moves({ verbose: true }).forEach((m) => {
      const from = m.from as Key;
      const to = m.to as Key;
      const d = dests.get(from) || [];
      d.push(to);
      dests.set(from, d);
    });
    return dests;
  };

  const handleMove = async (orig: string, dest: string) => {
    const activeSession = sessionRef.current;
    if (!activeSession || !cg) return;
    try {
      const lan = `${orig}${dest}`;
      chessRef.current.move({ from: orig, to: dest, promotion: 'q' });
      cg.set({ fen: chessRef.current.fen(), movable: { color: undefined } }); // Lock board while waiting

      const res = await ApiClient.makePlaySessionMove(activeSession.id, { move: lan });
      
      if (res.opponent_move) {
        const from = res.opponent_move.slice(0, 2);
        const to = res.opponent_move.slice(2, 4);
        const prom = res.opponent_move.length > 4 ? res.opponent_move.slice(4) : undefined;
        chessRef.current.move({ from, to, promotion: prom });
      }
      
      cg.set({
        fen: chessRef.current.fen(),
        movable: {
          color: 'white',
          dests: getDests(chessRef.current),
        },
      });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
      chessRef.current.undo();
      if (cg) {
        cg.set({ fen: chessRef.current.fen() });
      }
    }
  };

  return (
    <div className="play-screen" data-testid="play-screen">
      <h1>Play against Scan64</h1>
      {!session && (
        <div className="player-setup">
          <input 
            type="text" 
            placeholder="Player ID" 
            value={playerId} 
            onChange={e => setPlayerId(e.target.value)} 
            data-testid="player-id-input"
          />
          <input 
            type="text" 
            placeholder="Display Name" 
            value={playerName} 
            onChange={e => setPlayerName(e.target.value)} 
          />
          <button onClick={startGame} data-testid="start-btn">Start Game</button>
        </div>
      )}
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
