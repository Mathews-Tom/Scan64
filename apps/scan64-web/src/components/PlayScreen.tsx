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
import type { BotRead, CriticalInterruptionRead, PlayClockRead, PlaySessionRead, PlayMoveResponse, TimeControl } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { HintPanel, type HintPreview } from './HintPanel';
import { Chess } from 'chess.js';

import { Panel } from './Primitives';
const ACTIVE_PLAY_SESSION_STORAGE_KEY = 'scan64_active_play_session_id';
const TIME_CONTROLS: { label: string; control: TimeControl | null }[] = [
  { label: 'Untimed', control: null },
  { label: '1+0', control: { initial_seconds: 60, increment_seconds: 0 } },
  { label: '3+2', control: { initial_seconds: 180, increment_seconds: 2 } },
  { label: '5+0', control: { initial_seconds: 300, increment_seconds: 0 } },
  { label: '10+5', control: { initial_seconds: 600, increment_seconds: 5 } },
];

function PlayClocks({ clock, snapshotAt, onDeadline }: {
  clock: PlayClockRead; snapshotAt: number; onDeadline: (version: number) => void;
}) {
  const [elapsed, setElapsed] = useState(() => Math.max(0, performance.now() - snapshotAt));
  useEffect(() => {
    let frame = 0;
    let deadline: number | undefined;
    setElapsed(Math.max(0, performance.now() - snapshotAt));
    if (clock.active_color && clock.deadline_at && !clock.terminal_reason) {
      const remaining = clock.active_color === 'white' ? clock.white_remaining_ms : clock.black_remaining_ms;
      const tick = () => {
        const nextElapsed = Math.max(0, performance.now() - snapshotAt);
        setElapsed(nextElapsed);
        if (nextElapsed < remaining) frame = requestAnimationFrame(tick);
      };
      frame = requestAnimationFrame(tick);
      deadline = window.setTimeout(() => onDeadline(clock.version),
        Math.max(0, remaining - (performance.now() - snapshotAt)));
    }
    return () => {
      cancelAnimationFrame(frame);
      window.clearTimeout(deadline);
    };
  }, [clock, snapshotAt, onDeadline]);
  return <section aria-label="Game clocks">
    <p>{clock.initial_seconds}s per side · +{clock.increment_seconds}s increment. Hints and coach review do not pause the clock.</p>
    {(['white', 'black'] as const).map(color => {
      const active = clock.active_color === color;
      const balance = color === 'white' ? clock.white_remaining_ms : clock.black_remaining_ms;
      const seconds = Math.ceil(Math.max(0, balance - (active ? elapsed : 0)) / 1000);
      const label = color === 'white' ? 'White' : 'Black';
      return <div key={color} aria-label={`${label} clock${active ? ', active' : ''}`} data-testid={`${color}-clock`}>
        <strong>{label}{active ? ' · active' : ''}</strong>{' '}
        <time>{Math.floor(seconds / 60)}:{String(seconds % 60).padStart(2, '0')}</time>
      </div>;
    })}
  </section>;
}



export interface PlayScreenProps {
  initialSession?: PlaySessionRead;
  initialFen?: string;
  onNavigate?: (path: string) => void;
}

export function PlayScreen({ initialSession, initialFen, onNavigate }: PlayScreenProps = {}) {
  const [session, setSession] = useState<PlaySessionRead | null>(initialSession || null);
  const sessionRef = useRef<PlaySessionRead | null>(initialSession || null);
  const queuedMoveNeedsRefreshRef = useRef(new Set<string>());
  const movePendingRef = useRef(false);
  const opponentPendingRef = useRef(false);
  const resumedClockVersions = useRef(new Set<string>());
  const refreshedDeadlines = useRef(new Set<string>());
  const initialClockRefreshed = useRef(false);
  const [opponentPending, setOpponentPending] = useState(false);
  const [opponentRetry, setOpponentRetry] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [serverResult, setServerResult] = useState<string | null>(null);
  const [clockSnapshotAt, setClockSnapshotAt] = useState(() => performance.now());
  const [timeControlChoice, setTimeControlChoice] = useState('0');
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
  const [requestedSessionId, setRequestedSessionId] = useState(() => new URLSearchParams(window.location.search).get('session'));
  const preservedSessionId = useRef(
    requestedSessionId && localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) !== requestedSessionId
      ? localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) : null,
  );
  const consumeRequestedSession = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete('session');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    setRequestedSessionId(null);
  }, []);
  const [resuming, setResuming] = useState(
    () => !initialSession && (requestedSessionId !== null || localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) !== null),
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
    if (initialSession?.opponent_move && !initialSession.clock) {
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
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const updateOnline = () => setOnline(navigator.onLine);
    window.addEventListener('online', updateOnline);
    window.addEventListener('offline', updateOnline);
    return () => {
      window.removeEventListener('online', updateOnline);
      window.removeEventListener('offline', updateOnline);
    };
  }, []);
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
      
      const control = TIME_CONTROLS[Number(timeControlChoice)].control;
      const newSession = await ApiClient.createPlaySession({
        player_id: pid,
        opponent_config: { provider: 'stockfish', bot_level: botLevel },
        player_color: playerColor,
        coach_mode: coachMode,
        ...(control ? { clock_config: control } : {}),
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
      if (newSession.clock) await refreshQueuedSession(newSession.id);
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
  const refreshQueuedSession = useCallback(async (sessionId: string, generation = ++refreshGenerationRef.current) => {
    if (sessionRef.current?.id !== sessionId || refreshGenerationRef.current !== generation) return false;
    queuedMoveNeedsRefreshRef.current.add(sessionId);
    setRefreshing(true);
    updateBoard();
    try {
      const refreshedSession = await ApiClient.getPlaySession(sessionId);
      const receivedAt = performance.now();
      if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return false;
      const refreshedChess = new Chess();
      let result: string | null = null;
      if (!refreshedSession.game_id && chessRef.current.history().length > 0) {
        throw new Error('Missing game record.');
      }
      if (refreshedSession.game_id) {
        const game = await ApiClient.getGame(refreshedSession.game_id);
        if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return false;
        refreshedChess.loadPgn(game.pgn);
        result = game.result;
      }
      chessRef.current = refreshedChess;
      sessionRef.current = refreshedSession;
      queuedMoveNeedsRefreshRef.current.delete(sessionId);
      setSession(refreshedSession);
      setClockSnapshotAt(receivedAt);
      setServerResult(result);
      setHintPreview(null);
      setOpponentRetry(!!refreshedSession.clock?.active_color && refreshedSession.status === 'active' &&
        refreshedSession.clock.active_color !== sessionPlayerColor(refreshedSession) &&
        resumedClockVersions.current.has(`${sessionId}:${refreshedSession.clock.version}`));
      if (refreshedSession.status !== 'active') {
        criticalInterruptionRef.current = null;
        setCriticalInterruption(null);
      }
      setActivePlayerId(refreshedSession.player_id);
      updateBoard();
      setSyncRetrySessionId(null);
      setRefreshSessionId(null);
      setError(null);
      return true;
    } catch (error: unknown) {
      if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return false;
      setRefreshSessionId(sessionId);
      setError(`Unable to refresh the synchronized game: ${error instanceof Error ? error.message : 'Unknown error'}`);
      return false;
    } finally {
      if (refreshGenerationRef.current === generation) setRefreshing(false);
    }
  }, [updateBoard]);

  const onClockDeadline = useCallback((version: number) => {
    const current = sessionRef.current;
    if (!current?.clock || current.clock.version !== version || current.status !== 'active') return;
    const key = `${current.id}:${current.clock.version}`;
    if (refreshedDeadlines.current.has(key)) return;
    refreshedDeadlines.current.add(key);
    void refreshQueuedSession(current.id);
  }, [refreshQueuedSession]);

  const resumeOpponent = useCallback(async () => {
    const current = sessionRef.current;
    if (!current?.clock || current.status !== 'active' || !navigator.onLine ||
      !current.clock.active_color || current.clock.active_color === sessionPlayerColor(current) ||
      opponentPendingRef.current || movePendingRef.current ||
      queuedMoveNeedsRefreshRef.current.has(current.id)) return;
    resumedClockVersions.current.add(`${current.id}:${current.clock.version}`);
    opponentPendingRef.current = true;
    setOpponentPending(true);
    setOpponentRetry(false);
    setError(null);
    let failure: string | null = null;
    try {
      await ApiClient.resumePlaySessionOpponentMove(current.id, current.clock.version);
    } catch (cause) {
      failure = cause instanceof Error ? cause.message : 'Could not resume opponent move.';
    }
    const refreshed = await refreshQueuedSession(current.id);
    if (sessionRef.current?.id === current.id) {
      if (failure) setError(failure);
      setOpponentRetry(failure !== null && refreshed && sessionRef.current.status === 'active' &&
        sessionRef.current.clock?.active_color !== sessionPlayerColor(sessionRef.current));
    }
    opponentPendingRef.current = false;
    setOpponentPending(false);
  }, [refreshQueuedSession]);

  useEffect(() => {
    if (!initialSession?.clock || initialClockRefreshed.current) return;
    initialClockRefreshed.current = true;
    void refreshQueuedSession(initialSession.id);
  }, [initialSession, refreshQueuedSession]);

  useEffect(() => {
    if (!session?.clock || session.status !== 'active' || resuming || movePending ||
      opponentPending || !online || queuedMoveNeedsRefreshRef.current.has(session.id) ||
      !session.clock.active_color || session.clock.active_color === sessionPlayerColor(session)) return;
    if (!resumedClockVersions.current.has(`${session.id}:${session.clock.version}`)) void resumeOpponent();
  }, [session, resuming, movePending, opponentPending, online, resumeOpponent]);

  const synchronizeQueuedSession = useCallback((sessionId: string) => {
    if (sessionRef.current?.id !== sessionId) return;
    if (sessionRef.current.clock) {
      void refreshQueuedSession(sessionId);
      return;
    }
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
      movePendingRef.current || opponentPendingRef.current ||
      (activeSession.clock && (!navigator.onLine || activeSession.clock.active_color !== color)) ||
      (color !== undefined && chessRef.current.turn() !== (color === 'white' ? 'w' : 'b')) ||
      criticalInterruptionRef.current !== null ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
    let moveApplied = false;
    movePendingRef.current = true;
    setMovePending(true);
    try {
      chessRef.current.move({ from: lan.slice(0, 2), to: lan.slice(2, 4), promotion: lan[4] });
      moveApplied = true;
      updateBoard();
      try {
        const response = await ApiClient.makePlaySessionMove(activeSession.id, {
          move: lan,
          ...(activeSession.clock ? { expected_clock_version: activeSession.clock.version } : {}),
        });
        if (activeSession.clock) {
          await refreshQueuedSession(activeSession.id);
          if (response.critical_interruption && activeSession.coach_mode && sessionRef.current?.status === 'active') {
            criticalInterruptionRef.current = response.critical_interruption;
            setCriticalInterruption(response.critical_interruption);
          }
        } else applyMoveResponse(response);
      } catch (error: unknown) {
        if (!activeSession.clock && !navigator.onLine) {
          await queueMove(activeSession.id, lan);
          queuedMoveNeedsRefreshRef.current.add(activeSession.id);
          setError('Offline. Move queued. Waiting for network to resume game...');
          return;
        }
        throw error;
      }
    } catch (error: unknown) {
      if (activeSession.clock && moveApplied) {
        await refreshQueuedSession(activeSession.id);
      } else {
        if (moveApplied) chessRef.current.undo();
        updateBoard();
      }
      setError(error instanceof Error ? error.message : 'Unknown error');
    } finally {
      movePendingRef.current = false;
      setMovePending(false);
    }
  }, [applyMoveResponse, refreshQueuedSession, updateBoard]);

  const takeback = async () => {
    const activeSession = sessionRef.current;
    if (!activeSession || activeSession.status !== 'active' || activeSession.coach_mode || activeSession.clock ||
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
      if (activeSession.clock) await refreshQueuedSession(activeSession.id);
      setResigned(true);
      criticalInterruptionRef.current = null;
      setCriticalInterruption(null);
      if (!activeSession.clock) setError(null);
    } catch (caught) {
      if (activeSession.clock) await refreshQueuedSession(activeSession.id);
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

    const activeSessionId = requestedSessionId || localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    if (!activeSessionId) {
      setResuming(false);
      return;
    }

    setResuming(true);
    let cancelled = false;
    let sessionResolved = false;
    void (async () => {
      try {
        const resumedSession = await ApiClient.getPlaySession(activeSessionId);
        const receivedAt = performance.now();
        sessionResolved = true;
        if (cancelled || sessionRef.current) return;
        if (resumedSession.status !== 'active' && !resumedSession.clock) {
          if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === activeSessionId) {
            localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
          }
          if (requestedSessionId) consumeRequestedSession();
          return;
        }

        const resumedChess = new Chess();
        let result: string | null = null;
        if (resumedSession.game_id) {
          const game = await ApiClient.getGame(resumedSession.game_id);
          if (cancelled || sessionRef.current) return;
          result = game.result;
          resumedChess.loadPgn(game.pgn);
        }
        let hasQueuedMove = false;
        let queuedMoveError: string | null = null;
        if (!resumedSession.clock) {
          try {
            const queuedMoves = await getQueuedMoves();
            if (cancelled || sessionRef.current) return;
            hasQueuedMove = queuedMoves.some((queuedMove) => queuedMove.sessionId === resumedSession.id);
          } catch (error: unknown) {
            queuedMoveError = `Unable to inspect queued moves: ${
              error instanceof Error ? error.message : 'Unknown error'
            }`;
          }
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
        setServerResult(result);
        setClockSnapshotAt(receivedAt);
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
          if (requestedSessionId) consumeRequestedSession();
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
  }, [initialSession, requestedSessionId, consumeRequestedSession, synchronizeQueuedSession, updateBoard]);

  useEffect(() => {
    if (!currentSessionId || !currentSessionStatus) return;
    if (currentSessionStatus === 'active') {
      if (currentSessionId !== requestedSessionId || !preservedSessionId.current) {
        localStorage.setItem(ACTIVE_PLAY_SESSION_STORAGE_KEY, currentSessionId);
      }
    } else if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === currentSessionId) {
      localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    }
    if (currentSessionStatus !== 'active' && currentSessionId === requestedSessionId) {
      consumeRequestedSession();
    }
  }, [currentSessionId, currentSessionStatus, requestedSessionId, consumeRequestedSession]);

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
      if (currentSession.clock) return;
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
        if (sessionRef.current?.clock) void refreshQueuedSession(activeSessionId);
        else synchronizeQueuedSession(activeSessionId);
      }
    };
    window.addEventListener('online', synchronizeOnReconnect);
    return () => {
      window.removeEventListener('online', synchronizeOnReconnect);
    };
  }, [refreshQueuedSession, synchronizeQueuedSession]);
  const [sessionAnnouncement, setSessionAnnouncement] = useState('');
  const sessionId = session?.id;
  useEffect(() => {
    if (!sessionId) { setSessionAnnouncement(''); return; }
    const lastMove = chessRef.current.history().at(-1);
    const side = chessRef.current.turn() === 'w' ? 'White' : 'Black';
    setSessionAnnouncement(lastMove
      ? `Position loaded. Last move: ${lastMove}. ${side} to move.`
      : `Game started. ${side} to move.`);
  }, [sessionId]);


  return (
    <div className="play-screen working-screen" data-testid="play-screen">
      <h1 ref={heading} tabIndex={-1}>Play against Scan64</h1>
      <div className="visually-hidden" role="status" aria-label="Game position">{sessionAnnouncement}</div>
      {resuming ? <div role="status" data-testid="resuming-game">Resuming game...</div> : null}
      <div className={session ? 'board-workspace' : 'play-setup'}>
      {session && <div className="board-workspace__board">
      <CoachBoard
        {...boardPosition}
        {...(currentHint ? { fen: currentHint.fen, pgn: currentHint.fen === boardPosition.fen ? boardPosition.pgn : undefined, visualizations: currentHint.visualizations } : {})}
        testId="chessground-board"
        preview={currentHint !== null && currentHint.fen !== boardPosition.fen}
        orientation={sessionPlayerColor(session) ?? 'white'}
        flippable gameEnded={session.status !== 'active'}
        premoveColor={online && session.status === 'active' && !resigning && !takingBack &&
          criticalInterruption === null && !currentHint &&
          (!!session.clock || !queuedMoveNeedsRefreshRef.current.has(session.id))
          ? sessionPlayerColor(session) : undefined}
        disabled={session.status !== 'active' || movePending || opponentPending || resigning || takingBack ||
          (!!session.clock && (!online || session.clock.active_color !== sessionPlayerColor(session))) ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          criticalInterruption !== null || (currentHint !== null && currentHint.fen !== boardPosition.fen) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)}
        onMove={handleMove}
      />
      </div>}
      <div className="board-workspace__side">
      {!session && !resuming && (
        <Panel className="player-setup" title="Your opponent">
          {!getStoredPlayerId() && <><label htmlFor="play-player-id">Player ID</label>
          <input 
            type="text" 
            id="play-player-id"
            placeholder="Player ID" 
            value={playerId} 
            disabled={botChoicePending}
            onChange={e => setPlayerId(e.target.value)} 
            data-testid="player-id-input"
          /></>}
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
          <label>Time control
            <select aria-label="Time control" value={timeControlChoice}
              onChange={event => setTimeControlChoice(event.target.value)}>
              {TIME_CONTROLS.map((choice, index) => <option key={choice.label} value={String(index)}>{choice.label}</option>)}
            </select>
          </label>
          <button className="btn btn--primary" onClick={startGame} disabled={!botChoiceReady || botChoicePending} data-testid="start-btn">Start Game</button>
        </Panel>
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
      {session && <Panel title="Current game">
        <p>You play {sessionPlayerColor(session) ?? 'your chosen side'} against Scan64.</p>
        <p data-testid="session-info">Status: {session.status}</p>
      </Panel>}
      {session?.clock ? <PlayClocks key={`${session.id}:${session.clock.version}:${session.clock.server_now}`}
        clock={session.clock} snapshotAt={clockSnapshotAt} onDeadline={onClockDeadline} /> : null}
      {session?.clock && !online ? <p role="status">Timed moves require a connection and are never queued offline. The clock keeps running.</p> : null}
      {opponentPending ? <p role="status">Opponent thinking…</p> : null}
      {session?.clock && refreshing ? <p role="status">Refreshing game…</p> : null}
      {opponentRetry && session?.status === 'active' ? <button type="button"
        disabled={!online || opponentPending || queuedMoveNeedsRefreshRef.current.has(session.id)}
        onClick={() => void resumeOpponent()}>Retry opponent move</button> : null}
      {session?.status === 'active' && <HintPanel playerId={playerId} sessionId={session.id}
        fen={boardPosition.fen} onPreview={setHintPreview}
        disabled={movePending || opponentPending || resigning || takingBack || criticalInterruption !== null ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)} />}
      {session?.status === 'active' && <button type="button" onClick={() => void resign()}
        disabled={movePending || resigning || takingBack || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {resigning ? 'Resigning…' : 'Resign'}
      </button>}
      {session?.status === 'active' && !session.coach_mode && <button type="button"
        data-testid="takeback-btn" onClick={() => void takeback()}
        disabled={!!session.clock || movePending || resigning || takingBack || !navigator.onLine ||
          chessRef.current.history().length < 2 || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {takingBack ? 'Taking back…' : 'Take back'}
      </button>}
      {session?.clock && session.status === 'active' ? <p>Takebacks are unavailable in timed games.</p> : null}
      {session && session.status !== 'active' && <div role={resigned ? 'status' : undefined} data-testid="game-result">
        {session.clock ? <>
          {serverResult === '1-0' ? 'White wins.' : serverResult === '0-1' ? 'Black wins.'
            : serverResult === '1/2-1/2' ? 'Draw.' : 'Game ended. Awaiting the server result.'}
          {session.clock.terminal_reason === 'flag' ? ' Time expired.' : null}
        </> : resigned ? 'You resigned. Opponent wins.' : chessRef.current.isCheckmate()
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
      </div>
      </div>
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
    </div>
  );
}
