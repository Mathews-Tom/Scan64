import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import type { PlaySessionRead } from '../api/types';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';

function getDests(chess: Chess): Map<Key, Key[]> {
  const dests = new Map<Key, Key[]>();
  chess.moves({ verbose: true }).forEach((move) => {
    const from = move.from as Key;
    const to = move.to as Key;
    const destinations = dests.get(from) ?? [];
    destinations.push(to);
    dests.set(from, destinations);
  });
  return dests;
}


export interface PlayScreenProps {
  initialSession?: PlaySessionRead;
  initialFen?: string;
}

export function PlayScreen({ initialSession, initialFen }: PlayScreenProps = {}) {
  const boardRef = useRef<HTMLDivElement>(null);
  const [cg, setCg] = useState<Api | null>(null);
  const [session, setSession] = useState<PlaySessionRead | null>(initialSession || null);
  const sessionRef = useRef<PlaySessionRead | null>(initialSession || null);
  const cgRef = useRef<Api | null>(null);
  const [playerId, setPlayerId] = useState('');
  const [playerName, setPlayerName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const chessRef = useRef(new Chess(initialFen || 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'));



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
            color: chessRef.current.turn() === 'w' ? 'white' : 'black',
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


  const handleMove = useCallback(async (orig: string, dest: string) => {
    const activeSession = sessionRef.current;
    const board = cgRef.current;
    if (!activeSession || !board) return;

    try {
      const lan = `${orig}${dest}`;
      chessRef.current.move({ from: orig, to: dest, promotion: 'q' });
      board.set({ fen: chessRef.current.fen(), movable: { color: undefined } });

      const response = await ApiClient.makePlaySessionMove(activeSession.id, { move: lan });
      if (response.opponent_move) {
        const from = response.opponent_move.slice(0, 2);
        const to = response.opponent_move.slice(2, 4);
        const promotion =
          response.opponent_move.length > 4 ? response.opponent_move.slice(4) : undefined;
        chessRef.current.move({ from, to, promotion });
      }

      board.set({
        fen: chessRef.current.fen(),
        movable: {
          color: chessRef.current.turn() === 'w' ? 'white' : 'black',
          dests: getDests(chessRef.current),
        },
      });
    } catch (error: unknown) {
      setError(error instanceof Error ? error.message : 'Unknown error');
      chessRef.current.undo();
      board.set({
        fen: chessRef.current.fen(),
        movable: {
          color: chessRef.current.turn() === 'w' ? 'white' : 'black',
          dests: getDests(chessRef.current),
        },
      });
    }
  }, []);
  
  useEffect(() => {
    if (boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        fen: chessRef.current.fen(),
        movable: {
          color: sessionRef.current
            ? chessRef.current.turn() === 'w'
              ? 'white'
              : 'black'
            : undefined,
          free: false,
          dests: sessionRef.current ? getDests(chessRef.current) : undefined,
          events: {
            after: handleMove,
          },
        },
      });
      cgRef.current = api;
      setCg(api);
    }
  }, [cg, handleMove]);

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
