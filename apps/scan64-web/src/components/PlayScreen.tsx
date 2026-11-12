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
import { ApiClient, ApiRequestError, getStoredPlayerId, setActivePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL, sessionPlayerColor } from '../api/opponent';
import { CriticalMomentReview } from './CriticalMomentReview';
import type { BotRead, CriticalInterruptionRead, PlaySessionRead, PlayMoveResponse } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { HintPanel, type HintPreview } from './HintPanel';
import { Chess } from 'chess.js';

const ACTIVE_PLAY_SESSION_STORAGE_KEY = 'scan64_active_play_session_id';



export interface PlayScreenProps {
  initialSession?: PlaySessionRead;
  initialFen?: string;
  onNavigate?: (path: string) => void;
}

export function PlayScreen({ initialSession, initialFen, onNavigate }: PlayScreenProps = {}) {
  const [session, setSession] = useState<PlaySessionRead | null>(initialSession || null);
  const sessionRef = useRef<PlaySessionRead | null>(initialSession || null);
  const queuedMoveNeedsRefreshRef = useRef(new Set<string>());
  const refreshGenerationRef = useRef(0);
  const [playerId, setPlayerId] = useState(() => getStoredPlayerId() ?? '');
  const [coachMode, setCoachMode] = useState(false);
  const [bots, setBots] = useState<BotRead[]>([]);
  const [botLevel, setBotLevel] = useState(DEFAULT_BOT_LEVEL);
  const [botChoiceReady, setBotChoiceReady] = useState(() => !getStoredPlayerId());
  const [botChoicePending, setBotChoicePending] = useState(false);
  const [botChoiceError, setBotChoiceError] = useState<string | null>(null);
  const [botChoiceRequest, setBotChoiceRequest] = useState(0);
  const botChoiceBusy = useRef(false);
  useEffect(() => {
    if (!playerId || initialSession) return;
    let cancelled = false;
    setBotChoiceReady(false); setBotChoiceError(null);
    void ApiClient.getPlayerOnboarding(playerId).then(result => {
      if (cancelled) return;
      setBotLevel(String(result.manual_bot_level ?? result.suggested_bot_level ?? 1));
      setBotChoiceReady(true);
    }).catch(cause => {
      if (!cancelled) setBotChoiceError(cause instanceof Error ? cause.message : 'Could not load bot choice.');
    });
    return () => { cancelled = true; };
  }, [playerId, initialSession, botChoiceRequest]);

  const chooseBotLevel = async (value: string) => {
    if (botChoiceBusy.current) return;
    if (!playerId) return;
    botChoiceBusy.current = true;
    setBotChoicePending(true); setBotChoiceError(null);
    try {
      const result = await ApiClient.setPlayerBotChoice(playerId, Number(value));
      setBotLevel(String(result.manual_bot_level ?? result.preferred_bot_level));
    } catch (cause) {
      setBotChoiceError(cause instanceof Error ? cause.message : 'Could not save bot choice.');
    } finally { botChoiceBusy.current = false; setBotChoicePending(false); }
  };
  const [playerColor, setPlayerColor] = useState<'white' | 'black' | 'random'>('white');
  const [takingBack, setTakingBack] = useState(false);
  const takebackPendingRef = useRef(false);
  useEffect(() => {
    let cancelled = false;
    void ApiClient.getBots().then(result => { if (!cancelled) setBots(result); })
      .catch(caught => { if (!cancelled) setError(caught instanceof Error ? caught.message : 'Unable to load bots'); });
    return () => { cancelled = true; };
  }, []);
  const [syncRetrySessionId, setSyncRetrySessionId] = useState<string | null>(null);
  const [criticalInterruption, setCriticalInterruption] =
    useState<CriticalInterruptionRead | null>(null);
  const criticalInterruptionRef = useRef<CriticalInterruptionRead | null>(null);
  const [playerName, setPlayerName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [refreshSessionId, setRefreshSessionId] = useState<string | null>(null);
  const [resuming, setResuming] = useState(
    () => !initialSession && localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) !== null,
  );
  const currentSessionId = session?.id;
  const currentSessionStatus = session?.status;
  const heading = useRef<HTMLHeadingElement>(null);
  const interruptionPanel = useRef<HTMLDivElement>(null);
  const previousSession = useRef(currentSessionId);
  const previousInterruption = useRef<CriticalInterruptionRead | null>(null);
  useEffect(() => {
    if (criticalInterruption && criticalInterruption !== previousInterruption.current) interruptionPanel.current?.focus();
    else if (currentSessionId !== previousSession.current || (!criticalInterruption && previousInterruption.current)) heading.current?.focus();
    previousSession.current = currentSessionId;
    previousInterruption.current = criticalInterruption;
  }, [currentSessionId, criticalInterruption]);
  const [initialChess] = useState(() => {
    const chess = new Chess(initialFen);
    if (initialSession?.opponent_move) {
      const move = initialSession.opponent_move;
      chess.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
    }
    return chess;
  });
  const chessRef = useRef(initialChess);
  const [boardPosition, setBoardPosition] = useState(() => ({
    fen: chessRef.current.fen(), pgn: chessRef.current.pgn(),
  }));
  const [movePending, setMovePending] = useState(false);
  const [resigning, setResigning] = useState(false);
  const [resigned, setResigned] = useState(false);
  const [hintPreview, setHintPreview] = useState<HintPreview | null>(null);
  const currentHint = hintPreview?.sourceFen === boardPosition.fen ? hintPreview : null;
  const updateBoard = useCallback(() => {
    setBoardPosition({ fen: chessRef.current.fen(), pgn: chessRef.current.pgn() });
  }, []);



  const startGame = async () => {
    if (!botChoiceReady || botChoicePending) return;
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
        opponent_config: { provider: 'stockfish', bot_level: botLevel },
        player_color: playerColor,
        coach_mode: coachMode,
      });
      sessionRef.current = newSession;
      criticalInterruptionRef.current = null;
      setCriticalInterruption(null);
      setSession(newSession);
      chessRef.current.reset();
      if (newSession.opponent_move) {
        const move = newSession.opponent_move;
        chessRef.current.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
      }
      updateBoard();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    }
  };


  const applyMoveResponse = useCallback((response: PlayMoveResponse): boolean => {
    const currentSession = sessionRef.current;
    const interruption =
      currentSession?.coach_mode === true ? response.critical_interruption ?? null : null;
    if (interruption !== null) {
      criticalInterruptionRef.current = interruption;
      setCriticalInterruption(interruption);
    }
    if (response.opponent_move) {
      const from = response.opponent_move.slice(0, 2);
      const to = response.opponent_move.slice(2, 4);
      const promotion =
        response.opponent_move.length > 4 ? response.opponent_move.slice(4) : undefined;
      try {
        chessRef.current.move({ from, to, promotion });
      } catch (error: unknown) {
        if (currentSession) {
          queuedMoveNeedsRefreshRef.current.add(currentSession.id);
          setRefreshSessionId(currentSession.id);
        }
        setError(
          `Unable to apply the synchronized opponent move: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
        return false;
      }
    }

    if (currentSession) {
      const updatedSession = { ...currentSession, status: response.status };
      sessionRef.current = updatedSession;
      setSession(updatedSession);
      if (response.status !== 'active' && !updatedSession.game_id) {
        void (async () => {
          try {
            const persistedSession = await ApiClient.getPlaySession(updatedSession.id);
            if (sessionRef.current?.id !== updatedSession.id) return;
            const terminalSession = { ...sessionRef.current, game_id: persistedSession.game_id };
            sessionRef.current = terminalSession;
            setSession(terminalSession);
          } catch (caught) {
            if (sessionRef.current?.id !== updatedSession.id) return;
            setError(`Game ended, but its review could not be loaded: ${
              caught instanceof Error ? caught.message : 'Unknown error'
            }`);
          }
        })();
      }
    }
    updateBoard();
    setError(null);
    return true;
  }, [updateBoard]);
  const resumeAfterCriticalReview = useCallback(() => {
    criticalInterruptionRef.current = null;
    setCriticalInterruption(null);
    updateBoard();
  }, [updateBoard]);
  const refreshQueuedSession = useCallback((sessionId: string, generation = ++refreshGenerationRef.current) => {
    if (sessionRef.current?.id !== sessionId || refreshGenerationRef.current !== generation) return;
    queuedMoveNeedsRefreshRef.current.add(sessionId);
    updateBoard();
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
        updateBoard();
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
  }, [updateBoard]);

  const synchronizeQueuedSession = useCallback((sessionId: string) => {
    if (sessionRef.current?.id !== sessionId) return;
    const generation = ++refreshGenerationRef.current;
    queuedMoveNeedsRefreshRef.current.add(sessionId);
    updateBoard();
    setRefreshSessionId(null);
    void (async () => {
      try {
        await syncQueuedMoves();
        if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return;
        const queuedMoves = await getQueuedMoves();
        if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return;
        if (queuedMoves.some((queuedMove) => queuedMove.sessionId === sessionId)) {
          setSyncRetrySessionId(sessionId);
          setError('Queued move could not be synchronized. Reconnect to retry.');
          return;
        }
        setSyncRetrySessionId(null);
        refreshQueuedSession(sessionId, generation);
      } catch (error: unknown) {
        if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return;
        setSyncRetrySessionId(sessionId);
        setError(
          `Unable to synchronize queued moves: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
      }
    })();
  }, [refreshQueuedSession, updateBoard]);

  const handleMove = useCallback(async (lan: string) => {
    const activeSession = sessionRef.current;
    const color = sessionPlayerColor(activeSession);
    if (!activeSession || activeSession.status !== 'active' || takebackPendingRef.current ||
      (color !== undefined && chessRef.current.turn() !== (color === 'white' ? 'w' : 'b')) ||
      criticalInterruptionRef.current !== null ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
    let moveApplied = false;
    setMovePending(true);
    try {
      chessRef.current.move({ from: lan.slice(0, 2), to: lan.slice(2, 4), promotion: lan[4] });
      moveApplied = true;
      updateBoard();
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
      if (moveApplied) chessRef.current.undo();
      updateBoard();
    } finally {
      setMovePending(false);
    }
  }, [applyMoveResponse, updateBoard]);

  const takeback = async () => {
    const activeSession = sessionRef.current;
    if (!activeSession || activeSession.status !== 'active' || activeSession.coach_mode ||
      movePending || resigning || takebackPendingRef.current || !navigator.onLine ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id) ||
      chessRef.current.history().length < 2) return;
    takebackPendingRef.current = true;
    setTakingBack(true);
    let needsAuthoritativeRefresh = false;
    try {
      const queued = await getQueuedMoves();
      if (queued.some(move => move.sessionId === activeSession.id)) {
        queuedMoveNeedsRefreshRef.current.add(activeSession.id);
        setError('Synchronize queued moves before taking back.');
        return;
      }
      if (!navigator.onLine || sessionRef.current?.id !== activeSession.id ||
        queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
      const restoredSession = await ApiClient.takebackPlaySession(activeSession.id);
      needsAuthoritativeRefresh = true;
      if (!restoredSession.game_id) throw new Error('Takeback response is missing the game record.');
      const game = await ApiClient.getGame(restoredSession.game_id);
      if (sessionRef.current?.id !== activeSession.id) return;
      const restoredChess = new Chess();
      restoredChess.loadPgn(game.pgn);
      chessRef.current = restoredChess;
      sessionRef.current = restoredSession;
      setSession(restoredSession);
      updateBoard();
      setError(null);
    } catch (caught) {
      const definiteRejection = caught instanceof ApiRequestError && caught.status >= 400 && caught.status < 500;
      if ((needsAuthoritativeRefresh || !definiteRejection) && sessionRef.current?.id === activeSession.id) {
        queuedMoveNeedsRefreshRef.current.add(activeSession.id);
        setRefreshSessionId(activeSession.id);
      }
      setError(caught instanceof Error ? caught.message : 'Could not take back.');
    } finally {
      takebackPendingRef.current = false;
      setTakingBack(false);
    }
  };

  const resign = async () => {
    const activeSession = sessionRef.current;
    if (!activeSession || activeSession.status !== 'active' || movePending || resigning || takebackPendingRef.current ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
    setResigning(true);
    try {
      const endedSession = await ApiClient.resignPlaySession(activeSession.id);
      sessionRef.current = endedSession;
      setSession(endedSession);
      setResigned(true);
      criticalInterruptionRef.current = null;
      setCriticalInterruption(null);
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not resign.');
    } finally {
      setResigning(false);
    }
  };

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
        updateBoard();
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
  }, [initialSession, synchronizeQueuedSession, updateBoard]);

  useEffect(() => {
    if (!currentSessionId || !currentSessionStatus) return;
    if (currentSessionStatus === 'active') {
      localStorage.setItem(ACTIVE_PLAY_SESSION_STORAGE_KEY, currentSessionId);
    } else if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === currentSessionId) {
      localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    }
  }, [currentSessionId, currentSessionStatus]);

  useEffect(() => {
    const testWindow = window as unknown as Record<string, unknown>;
    if (!import.meta.env.DEV || testWindow.__e2e_offline_move !== true) return;
    testWindow.__e2e_move = async (from = 'e2', to = 'e4') => {
      await handleMove(`${from}${to}`);
    };
    return () => {
      delete testWindow.__e2e_move;
    };
  }, [handleMove]);

  useEffect(() => {
    const handleSyncSuccess = (event: Event) => {
      const { queuedMove, response } =
        (event as CustomEvent<QueuedMoveSyncSuccess>).detail;
      const currentSession = sessionRef.current;
      if (queuedMove.sessionId !== currentSession?.id) return;
      queuedMoveNeedsRefreshRef.current.add(queuedMove.sessionId);
      applyMoveResponse(response);
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
      refreshGenerationRef.current += 1;
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


  return (
    <div className="play-screen" data-testid="play-screen">
      <h1 ref={heading} tabIndex={-1}>Play against Scan64</h1>
      {resuming ? <div role="status" data-testid="resuming-game">Resuming game...</div> : null}
      {!session && !resuming && (
        <div className="player-setup">
          <label htmlFor="play-player-id">Player ID</label>
          <input 
            type="text" 
            id="play-player-id"
            placeholder="Player ID" 
            value={playerId} 
            disabled={botChoicePending}
            readOnly={getStoredPlayerId() !== null}
            onChange={e => setPlayerId(e.target.value)} 
            data-testid="player-id-input"
          />
          <label htmlFor="play-display-name">Display name (optional)</label>
          <input 
            type="text" 
            id="play-display-name"
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
          <label>Bot level
            <select data-testid="bot-level-select" value={botLevel} disabled={!playerId || !botChoiceReady || botChoicePending}
              onChange={event => void chooseBotLevel(event.target.value)}>
              {bots.map(bot => <option key={bot.level} value={String(bot.level)}>{bot.level}. {bot.name}</option>)}
            </select>
          </label>
          {bots.find(bot => String(bot.level) === botLevel)?.description}
          {!botChoiceReady && !botChoiceError ? <p role="status">Loading your bot choice…</p> : null}
          {botChoicePending ? <p role="status">Saving bot choice…</p> : null}
          {botChoiceError ? <div role="alert"><p>{botChoiceError}</p><button onClick={() => setBotChoiceRequest(value => value + 1)}>Retry bot choice</button></div> : null}
          <p>Your last manual choice takes priority over your starting suggestion. Bot levels are not chess Elo.</p>
          <label>Play as
            <select data-testid="player-color-select" value={playerColor}
              onChange={event => setPlayerColor(event.target.value as 'white' | 'black' | 'random')}>
              <option value="white">White</option><option value="black">Black</option><option value="random">Random</option>
            </select>
          </label>
          <button onClick={startGame} disabled={!botChoiceReady || botChoicePending} data-testid="start-btn">Start Game</button>
        </div>
      )}
      {error && <div className="error" role="alert">{error}</div>}
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
      <CoachBoard
        {...boardPosition}
        {...(currentHint ? { fen: currentHint.fen, pgn: currentHint.fen === boardPosition.fen ? boardPosition.pgn : undefined, visualizations: currentHint.visualizations } : {})}
        testId="chessground-board"
        preview={currentHint !== null && currentHint.fen !== boardPosition.fen}
        orientation={sessionPlayerColor(session) ?? 'white'}
        disabled={!session || session.status !== 'active' || movePending || resigning || takingBack ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          criticalInterruption !== null || (currentHint !== null && currentHint.fen !== boardPosition.fen) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)}
        onMove={handleMove}
      />
      {session?.status === 'active' && <HintPanel playerId={playerId} sessionId={session.id}
        fen={boardPosition.fen} onPreview={setHintPreview}
        disabled={movePending || resigning || takingBack || criticalInterruption !== null ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)} />}
      {session?.status === 'active' && <button type="button" onClick={() => void resign()}
        disabled={movePending || resigning || takingBack || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {resigning ? 'Resigning…' : 'Resign'}
      </button>}
      {session?.status === 'active' && !session.coach_mode && <button type="button"
        data-testid="takeback-btn" onClick={() => void takeback()}
        disabled={movePending || resigning || takingBack || !navigator.onLine ||
          chessRef.current.history().length < 2 || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {takingBack ? 'Taking back…' : 'Take back'}
      </button>}
      {session && session.status !== 'active' && <div role={resigned ? 'status' : undefined} data-testid="game-result">
        {resigned ? 'You resigned. Opponent wins.' : chessRef.current.isCheckmate()
          ? `Checkmate. ${chessRef.current.turn() === 'w' ? 'Black' : 'White'} wins.`
          : chessRef.current.isStalemate() ? 'Draw by stalemate.'
          : chessRef.current.isDraw() ? 'Draw.' : 'Game ended.'}
        {session.game_id && <> <a href={`/games/${encodeURIComponent(session.game_id)}/analysis`}
          onClick={event => {
            if (!onNavigate || !session.game_id || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
            event.preventDefault();
            onNavigate(`/games/${encodeURIComponent(session.game_id)}/analysis`);
          }}>Review game</a></>}
      </div>}
      {criticalInterruption ? (
        <div ref={interruptionPanel} tabIndex={-1} role="region" aria-label="Coach review">
        <CriticalMomentReview
          lesson={criticalInterruption.lesson}
          opportunityId={criticalInterruption.opportunity_id}
          sessionId={criticalInterruption.study_session_id}
          onComplete={resumeAfterCriticalReview}
        />
        </div>
      ) : null}
      {session && (
        <div data-testid="session-info">
          Status: {session.status}
        </div>
      )}
    </div>
  );
}
