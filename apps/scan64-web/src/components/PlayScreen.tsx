import {
  getQueuedMoves,
  QUEUED_MOVE_SYNC_FAILED,
  QUEUED_MOVE_SYNC_SUCCEEDED,
  queueMove,
  syncQueuedMoves,
  type QueuedMoveSyncFailure,
  type QueuedMoveSyncSuccess,
} from '../api/offlineQueue';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ApiClient, ApiRequestError, setActivePlayerId } from '../api/client';
import type { PlaySessionRead, PlayMoveResponse } from '../api/types';
import { Chessground } from 'chessground';
import type { Api } from 'chessground/api';
import type { Key } from 'chessground/types';
import 'chessground/assets/chessground.base.css';
import 'chessground/assets/chessground.brown.css';
import 'chessground/assets/chessground.cburnett.css';
import { Chess } from 'chess.js';

const ACTIVE_PLAY_SESSION_STORAGE_KEY = 'scan64_active_play_session_id';

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

function chessgroundTurnColor(chess: Chess): 'white' | 'black' {
  return chess.turn() === 'w' ? 'white' : 'black';
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
  const queuedMoveNeedsRefreshRef = useRef(new Set<string>());
  const refreshGenerationRef = useRef(0);
  const [playerId, setPlayerId] = useState('');
  const [coachMode, setCoachMode] = useState(false);
  const [syncRetrySessionId, setSyncRetrySessionId] = useState<string | null>(null);
  const [playerName, setPlayerName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [refreshSessionId, setRefreshSessionId] = useState<string | null>(null);
  const [resuming, setResuming] = useState(
    () => !initialSession && localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) !== null,
  );
  const currentSessionId = session?.id;
  const currentSessionStatus = session?.status;
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
      setActivePlayerId(pid);
      
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
          turnColor: chessgroundTurnColor(chessRef.current),
          movable: {
            color: chessgroundTurnColor(chessRef.current),
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


  const applyMoveResponse = useCallback((response: PlayMoveResponse) => {
    const board = cgRef.current;
    if (!board) return;

    const currentSession = sessionRef.current;
    if (response.opponent_move) {
      const from = response.opponent_move.slice(0, 2);
      const to = response.opponent_move.slice(2, 4);
      const promotion =
        response.opponent_move.length > 4 ? response.opponent_move.slice(4) : undefined;
      try {
        chessRef.current.move({ from, to, promotion });
      } catch (error: unknown) {
        if (currentSession) {
          setRefreshSessionId(currentSession.id);
        }
        setError(
          `Unable to apply the synchronized opponent move: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
        return;
      }
    }

    if (currentSession) {
      const updatedSession = { ...currentSession, status: response.status };
      sessionRef.current = updatedSession;
      setSession(updatedSession);
    }
    board.set({
      fen: chessRef.current.fen(),
      turnColor: chessgroundTurnColor(chessRef.current),
      movable: {
        color: response.status === 'active' ? chessgroundTurnColor(chessRef.current) : undefined,
        dests: response.status === 'active' ? getDests(chessRef.current) : undefined,
      },
    });
    setError(null);
  }, []);

  const refreshQueuedSession = useCallback((sessionId: string) => {
    const generation = refreshGenerationRef.current + 1;
    refreshGenerationRef.current = generation;
    void (async () => {
      try {
        const refreshedSession = await ApiClient.getPlaySession(sessionId);
        if (
          refreshGenerationRef.current !== generation ||
          sessionRef.current?.id !== sessionId
        ) return;

        const refreshedChess = new Chess();
        if (!refreshedSession.game_id && chessRef.current.history().length > 0) {
          setRefreshSessionId(sessionId);
          setError('Unable to refresh the synchronized game: missing game record.');
          return;
        }
        if (refreshedSession.game_id) {
          const game = await ApiClient.getGame(refreshedSession.game_id);
          if (
            refreshGenerationRef.current !== generation ||
            sessionRef.current?.id !== sessionId
          ) return;
          refreshedChess.loadPgn(game.pgn);
        }

        chessRef.current = refreshedChess;
        sessionRef.current = refreshedSession;
        queuedMoveNeedsRefreshRef.current.delete(sessionId);
        setSession(refreshedSession);
        setActivePlayerId(refreshedSession.player_id);
        cgRef.current?.set({
          fen: refreshedChess.fen(),
          turnColor: chessgroundTurnColor(refreshedChess),
          movable: {
            color:
              refreshedSession.status === 'active'
                ? chessgroundTurnColor(refreshedChess)
                : undefined,
            dests:
              refreshedSession.status === 'active' ? getDests(refreshedChess) : undefined,
          },
        });
        setSyncRetrySessionId(null);
        setRefreshSessionId(null);
        setError(null);
      } catch (error: unknown) {
        if (
          refreshGenerationRef.current !== generation ||
          sessionRef.current?.id !== sessionId
        ) return;
        setRefreshSessionId(sessionId);
        setError(
          `Unable to refresh the synchronized game: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
      }
    })();
  }, []);

  const synchronizeQueuedSession = useCallback((sessionId: string) => {
    setRefreshSessionId(null);
    void (async () => {
      try {
        await syncQueuedMoves();
        const queuedMoves = await getQueuedMoves();
        if (sessionRef.current?.id !== sessionId) return;
        if (queuedMoves.some((queuedMove) => queuedMove.sessionId === sessionId)) {
          setSyncRetrySessionId(sessionId);
          setError('Queued move could not be synchronized. Reconnect to retry.');
          return;
        }
        setSyncRetrySessionId(null);
        refreshQueuedSession(sessionId);
      } catch (error: unknown) {
        if (sessionRef.current?.id !== sessionId) return;
        setSyncRetrySessionId(sessionId);
        setError(
          `Unable to synchronize queued moves: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
      }
    })();
  }, [refreshQueuedSession]);

  const handleMove = useCallback(async (orig: string, dest: string) => {
    const activeSession = sessionRef.current;
    const board = cgRef.current;
    if (!activeSession || !board) return;
    let moveApplied = false;

    try {
      const lan = `${orig}${dest}`;
      chessRef.current.move({ from: orig, to: dest, promotion: 'q' });
      moveApplied = true;
      board.set({
        fen: chessRef.current.fen(),
        turnColor: chessgroundTurnColor(chessRef.current),
        movable: { color: undefined },
      });

      try {
        const response = await ApiClient.makePlaySessionMove(activeSession.id, { move: lan });
        applyMoveResponse(response);
      } catch (error: unknown) {
        if (!navigator.onLine) {
          await queueMove(activeSession.id, lan);
          queuedMoveNeedsRefreshRef.current.add(activeSession.id);
          setError('Offline. Move queued. Waiting for network to resume game...');
          return;
        }
        throw error;
      }
    } catch (error: unknown) {
      setError(error instanceof Error ? error.message : 'Unknown error');
      if (moveApplied) {
        chessRef.current.undo();
      }
      board.set({
        fen: chessRef.current.fen(),
        turnColor: chessgroundTurnColor(chessRef.current),
        movable: {
          color: chessgroundTurnColor(chessRef.current),
          dests: getDests(chessRef.current),
        },
      });
    }
  }, [applyMoveResponse]);

  useEffect(() => {
    if (initialSession || sessionRef.current) {
      setResuming(false);
      return;
    }

    const activeSessionId = localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    if (!activeSessionId) {
      setResuming(false);
      return;
    }

    let cancelled = false;
    let sessionResolved = false;
    void (async () => {
      try {
        const resumedSession = await ApiClient.getPlaySession(activeSessionId);
        sessionResolved = true;
        if (cancelled || sessionRef.current) return;
        if (resumedSession.status !== 'active') {
          if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === activeSessionId) {
            localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
          }
          return;
        }

        const resumedChess = new Chess();
        if (resumedSession.game_id) {
          const game = await ApiClient.getGame(resumedSession.game_id);
          if (cancelled || sessionRef.current) return;
          resumedChess.loadPgn(game.pgn);
        }
        let hasQueuedMove = false;
        let queuedMoveError: string | null = null;
        try {
          const queuedMoves = await getQueuedMoves();
          if (cancelled || sessionRef.current) return;
          hasQueuedMove = queuedMoves.some((queuedMove) => queuedMove.sessionId === resumedSession.id);
        } catch (error: unknown) {
          queuedMoveError = `Unable to inspect queued moves: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`;
        }
        if (cancelled || sessionRef.current) return;
        const needsRefresh = hasQueuedMove || queuedMoveError !== null;

        chessRef.current = resumedChess;
        sessionRef.current = resumedSession;
        if (needsRefresh) {
          queuedMoveNeedsRefreshRef.current.add(resumedSession.id);
        } else {
          queuedMoveNeedsRefreshRef.current.delete(resumedSession.id);
        }
        setSession(resumedSession);
        setActivePlayerId(resumedSession.player_id);
        cgRef.current?.set({
          fen: resumedChess.fen(),
          turnColor: chessgroundTurnColor(resumedChess),
          movable: {
            color: needsRefresh ? undefined : chessgroundTurnColor(resumedChess),
            dests: needsRefresh ? undefined : getDests(resumedChess),
          },
        });
        setError(
          queuedMoveError ??
            (hasQueuedMove ? 'Queued move is waiting to synchronize. Reconnect to resume the game.' : null),
        );
        if (needsRefresh && navigator.onLine) {
          synchronizeQueuedSession(resumedSession.id);
        }
      } catch (error: unknown) {
        if (cancelled || sessionRef.current) return;
        if (
          !sessionResolved &&
          error instanceof ApiRequestError &&
          (error.status === 404 || error.status === 422)
        ) {
          if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === activeSessionId) {
            localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
          }
          setError('Previous game is no longer available.');
          return;
        }
        setError(`Unable to resume game: ${error instanceof Error ? error.message : 'Unknown error'}`);
      } finally {
        if (!cancelled) setResuming(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [initialSession, synchronizeQueuedSession]);

  useEffect(() => {
    if (!currentSessionId || !currentSessionStatus) return;
    if (currentSessionStatus === 'active') {
      localStorage.setItem(ACTIVE_PLAY_SESSION_STORAGE_KEY, currentSessionId);
    } else if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === currentSessionId) {
      localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    }
  }, [currentSessionId, currentSessionStatus]);

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    (window as unknown as Record<string, unknown>).__e2e_move = async (
      from = 'e2',
      to = 'e4',
    ) => {
      await handleMove(from, to);
    };
    return () => {
      delete (window as unknown as Record<string, unknown>).__e2e_move;
    };
  }, [handleMove]);

  useEffect(() => {
    const handleSyncSuccess = (event: Event) => {
      const { queuedMove, response } =
        (event as CustomEvent<QueuedMoveSyncSuccess>).detail;
      const currentSession = sessionRef.current;
      if (queuedMove.sessionId !== currentSession?.id) return;

      if (!queuedMoveNeedsRefreshRef.current.has(queuedMove.sessionId)) {
        applyMoveResponse(response);
      }
    };
    const handleSyncFailure = (event: Event) => {
      const { queuedMove, message } =
        (event as CustomEvent<QueuedMoveSyncFailure>).detail;
      if (!queuedMove || queuedMove.sessionId === sessionRef.current?.id) {
        if (queuedMove && queuedMoveNeedsRefreshRef.current.has(queuedMove.sessionId)) {
          setSyncRetrySessionId(queuedMove.sessionId);
        }
        setError(`Queued move could not be synchronized: ${message}. Reconnect to retry.`);
      }
    };

    window.addEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, handleSyncSuccess);
    window.addEventListener(QUEUED_MOVE_SYNC_FAILED, handleSyncFailure);
    return () => {
      window.removeEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, handleSyncSuccess);
      window.removeEventListener(QUEUED_MOVE_SYNC_FAILED, handleSyncFailure);
    };
  }, [applyMoveResponse]);

  useEffect(() => {
    const synchronizeOnReconnect = () => {
      const activeSessionId = sessionRef.current?.id;
      if (activeSessionId && queuedMoveNeedsRefreshRef.current.has(activeSessionId)) {
        synchronizeQueuedSession(activeSessionId);
      }
    };
    window.addEventListener('online', synchronizeOnReconnect);
    return () => {
      window.removeEventListener('online', synchronizeOnReconnect);
    };
  }, [synchronizeQueuedSession]);


  // Starting a session unmounts the setup form, so the board shifts on screen.
  // Chessground caches its DOM bounds and would otherwise map pointer events to
  // the pre-shift rectangle, leaving the board unresponsive to clicks and drags.
  useEffect(() => {
    if (!session?.id) return;
    cgRef.current?.redrawAll();
  }, [session?.id]);


  useEffect(() => {
    if (boardRef.current && !cg) {
      const api = Chessground(boardRef.current, {
        fen: chessRef.current.fen(),
        turnColor: chessgroundTurnColor(chessRef.current),
        movable: {
          color: sessionRef.current ? chessgroundTurnColor(chessRef.current) : undefined,
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
      {resuming ? <div data-testid="resuming-game">Resuming game...</div> : null}
      {!session && !resuming && (
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
          <label>
            <input 
              type="checkbox" 
              checked={coachMode} 
              onChange={e => setCoachMode(e.target.checked)} 
              data-testid="coach-mode-toggle"
            />
            Coach Mode
          </label>
          <button onClick={startGame} data-testid="start-btn">Start Game</button>
        </div>
      )}
      {error && <div className="error">{error}</div>}
      {syncRetrySessionId ? (
        <button
          type="button"
          data-testid="retry-move-sync"
          onClick={() => synchronizeQueuedSession(syncRetrySessionId)}
        >
          Retry move synchronization
        </button>
      ) : null}
      {refreshSessionId ? (
        <button
          type="button"
          data-testid="retry-game-refresh"
          onClick={() => refreshQueuedSession(refreshSessionId)}
        >
          Retry game refresh
        </button>
      ) : null}
      <div style={{ display: 'flex', gap: '20px' }}>
        <div 
          ref={boardRef} 
          style={{ width: '400px', height: '400px' }} 
          data-testid="chessground-board" 
        />
      </div>
      {session && (
        <div data-testid="session-info">
          Status: {session.status}
        </div>
      )}
    </div>
  );
}
