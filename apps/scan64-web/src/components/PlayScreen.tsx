import {
  getQueuedMoves,
  QUEUED_MOVE_SYNC_FAILED,
  QUEUED_MOVE_SYNC_SUCCEEDED,
  queueMove,
  removeQueuedMove,
  syncQueuedMoves,
  type QueuedMoveSyncFailure,
  type QueuedMove,
  type QueuedMoveSyncSuccess,
} from '../api/offlineQueue';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ApiClient, ApiRequestError, BotPlayUnavailableError, ensurePlayerAuthorization, getStoredPlayerId, setActivePlayerId } from '../api/client';
import { DEFAULT_BOT_LEVEL, sessionPlayerColor } from '../api/opponent';
import { CriticalMomentReview } from './CriticalMomentReview';
import type { AssistanceMode, BotRead, CriticalInterruptionRead, PlayAssistanceMode, PlayAssistanceRead, PlayClockRead, PlaySessionRead, PlayMoveResponse, TimeControl } from '../api/types';
import { CoachBoard } from './CoachBoard';
import { HintPanel, type HintPreview } from './HintPanel';
import { PlayHealth } from './PlayHealth';
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

interface AssistanceSource {
  sessionId: string;
  playerId: string;
  fen: string;
  revision: number;
  clockVersion?: number;
}

type AssistanceState = {
  source: AssistanceSource;
  mode: PlayAssistanceMode;
} & ({ pending: true } | { pending: false; result: PlayAssistanceRead });

export interface PlayScreenProps {
  initialSession?: PlaySessionRead;
  initialFen?: string;
  onNavigate?: (path: string) => void;
}

// previousFen: the position before the last move, for the health meters' rise or fall.
function readBoardPosition(chess: Chess): { fen: string; pgn: string; previousFen: string | null } {
  const history = chess.history({ verbose: true });
  return { fen: chess.fen(), pgn: chess.pgn(), previousFen: history.length ? history[history.length - 1].before : null };
}

export function PlayScreen({ initialSession, initialFen, onNavigate }: PlayScreenProps = {}) {
  const [session, setSession] = useState<PlaySessionRead | null>(initialSession || null);
  const sessionRef = useRef<PlaySessionRead | null>(initialSession || null);
  const queuedMoveNeedsRefreshRef = useRef(new Set<string>());
  const movePendingRef = useRef(false);
  const opponentPendingRef = useRef(false);
  const failedOpponentVersions = useRef(new Set<string>());
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
  const [botLevel, setBotLevel] = useState(initialSession?.opponent_config.bot_level ?? DEFAULT_BOT_LEVEL);
  const [botChoiceReady, setBotChoiceReady] = useState(() => !getStoredPlayerId());
  const [botChoicePending, setBotChoicePending] = useState(false);
  const [botChoiceError, setBotChoiceError] = useState<string | null>(null);
  const [botChoiceRequest, setBotChoiceRequest] = useState(0);
  const botChoiceBusy = useRef(false);
  const startPendingRef = useRef(false);
  const [startingGame, setStartingGame] = useState(false);
  useEffect(() => {
    if (!playerId || initialSession || sessionRef.current?.sparring) return;
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
  }, [playerId, initialSession, botChoiceRequest, session?.sparring]);

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
  const [legacyQueuedMoves, setLegacyQueuedMoves] = useState<QueuedMove[]>([]);
  const [legacyDiscard, setLegacyDiscard] = useState<{
    sessionId: string; playerId: string; moves: QueuedMove[];
  } | null>(null);
  const [discardingLegacy, setDiscardingLegacy] = useState(false);
  const legacyDiscardPendingRef = useRef(false);
  const [criticalInterruption, setCriticalInterruption] =
    useState<CriticalInterruptionRead | null>(null);
  const criticalInterruptionRef = useRef<CriticalInterruptionRead | null>(null);
  const timedInterruptionRef = useRef<{
    sessionId: string; playerId: string; clockVersion: number; interruption: CriticalInterruptionRead;
  } | null>(null);
  const [playerName, setPlayerName] = useState('');
  const [error, setError] = useState<string | null>(null);
  // Survives board refreshes, which clear `error`; a new game clears it.
  const [botPlayNotice, setBotPlayNotice] = useState<string | null>(null);
  const [conversionRefreshing, setConversionRefreshing] = useState(false);
  const [conversionRefreshFailed, setConversionRefreshFailed] = useState(false);
  const [conversionRetrying, setConversionRetrying] = useState(false);
  const conversionRetryPending = useRef(false);
  const [refreshSessionId, setRefreshSessionId] = useState<string | null>(null);
  const [requestedSessionId, setRequestedSessionId] = useState(() => new URLSearchParams(window.location.search).get('session'));
  const preservedSessionId = useRef(
    requestedSessionId && localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) !== requestedSessionId
      ? localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) : null,
  );
  const [restoreRetry, setRestoreRetry] = useState<{ sessionId: string } | null>(null);
  const consumeRequestedSession = useCallback(() => {
    const url = new URL(window.location.href);
    url.searchParams.delete('session');
    window.history.replaceState(window.history.state, '', `${url.pathname}${url.search}${url.hash}`);
    setRestoreRetry(null);
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
    const chess = new Chess(initialSession?.sparring?.source_fen ?? initialFen);
    if (initialSession?.opponent_move && !initialSession.clock) {
      const move = initialSession.opponent_move;
      chess.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
    }
    return chess;
  });
  const chessRef = useRef(initialChess);
  const [boardPosition, setBoardPosition] = useState(() => readBoardPosition(chessRef.current));
  const [movePending, setMovePending] = useState(false);
  const positionRevision = useRef(0);
  const assistanceRequest = useRef(0);
  const [assistance, setAssistance] = useState<AssistanceState | null>(null);
  const assistanceRef = useRef<AssistanceState | null>(null);
  const [assistanceError, setAssistanceError] = useState<string | null>(null);
  const [candidateMoves, setCandidateMoves] = useState<string[]>([]);
  const [guardEnabled, setGuardEnabled] = useState(false);
  const guardEnabledRef = useRef(guardEnabled);
  guardEnabledRef.current = guardEnabled;
  const guardToggle = useRef<HTMLInputElement>(null);
  const guardCancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (assistance && !assistance.pending && assistance.mode === 'guard') guardCancel.current?.focus();
  }, [assistance]);
  useEffect(() => () => { positionRevision.current += 1; }, []);
  const clearAssistance = useCallback(() => {
    assistanceRequest.current += 1;
    assistanceRef.current = null;
    setAssistance(null);
    setAssistanceError(null);
  }, []);
  const legalCandidates = useMemo(() => new Chess(boardPosition.fen).moves({ verbose: true }).map(move => ({
    move: `${move.from}${move.to}${move.promotion ?? ''}`, san: move.san,
  })), [boardPosition.fen]);
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
    positionRevision.current += 1;
    clearAssistance();
    setCandidateMoves([]);
    setBoardPosition(readBoardPosition(chessRef.current));
  }, [clearAssistance]);
  useEffect(() => {
    clearAssistance();
    setCandidateMoves([]);
    if (!online) setAssistanceError('Candidates and blunder guard require a connection. Protected moves are never queued.');
    return () => { assistanceRequest.current += 1; };
  }, [online, currentSessionId, currentSessionStatus, session?.player_id, clearAssistance]);
  const handleHintPreview = useCallback((preview: HintPreview | null) => {
    setHintPreview(preview);
    const activeSession = sessionRef.current;
    if (!preview || !activeSession) return;
    clearAssistance();
    if (!activeSession.assistance_modes.includes('hint')) {
      const updated = { ...activeSession, assistance_modes: [...activeSession.assistance_modes, 'hint' as const] };
      sessionRef.current = updated;
      setSession(updated);
    }
  }, [clearAssistance]);

  // A counted health reading (#417) is hint use: show it with the other assistance.
  const handleHealthAssistance = useCallback((modes: AssistanceMode[]) => {
    const activeSession = sessionRef.current;
    if (!activeSession) return;
    const updated = { ...activeSession, assistance_modes: modes };
    sessionRef.current = updated;
    setSession(updated);
  }, []);

  const refreshConversionResult = useCallback(async (sessionId: string) => {
    setConversionRefreshing(true);
    setConversionRefreshFailed(false);
    try {
      const fresh = await ApiClient.getPlaySession(sessionId);
      if (sessionRef.current?.id !== sessionId) return;
      // Initially linked terminal conversions still start at the source FEN.
      if (initialSession?.sparring && initialSession.id === sessionId && initialSession.status !== 'active') {
        if (!fresh.game_id) throw new Error('Conversion game record is unavailable.');
        const game = await ApiClient.getGame(fresh.game_id);
        if (sessionRef.current?.id !== sessionId) return;
        const restoredChess = new Chess();
        restoredChess.loadPgn(game.pgn);
        chessRef.current = restoredChess;
        updateBoard();
      }
      sessionRef.current = fresh;
      setSession(fresh);
      setError(null);
    } catch (caught) {
      if (sessionRef.current?.id !== sessionId) return;
      setConversionRefreshFailed(true);
      setError(caught instanceof Error ? caught.message : 'Could not load conversion result.');
    } finally {
      if (sessionRef.current?.id === sessionId) setConversionRefreshing(false);
    }
  }, [initialSession, updateBoard]);
  const isConversion = Boolean(session?.sparring);
  useEffect(() => {
    if (isConversion && currentSessionId && currentSessionStatus !== 'active') {
      void refreshConversionResult(currentSessionId);
    }
  }, [isConversion, currentSessionId, currentSessionStatus, refreshConversionResult]);

  const retryConversion = async () => {
    const previous = sessionRef.current;
    if (!previous?.sparring || previous.status === 'active' || conversionRetryPending.current ||
      conversionRefreshing || conversionRefreshFailed) return;
    conversionRetryPending.current = true;
    setConversionRetrying(true);
    setError(null);
    try {
      const pid = await ensurePlayerAuthorization(previous.player_id);
      const fresh = await ApiClient.createPlaySession({
        player_id: pid,
        opponent_config: { provider: 'stockfish', bot_level: botLevel },
        sparring_source: { game_id: previous.sparring.source_game_id, ply: previous.sparring.source_ply },
      });
      if (!fresh.sparring) throw new Error('Conversion practice metadata is unavailable.');
      const chess = new Chess(fresh.sparring.source_fen);
      if (fresh.opponent_move) chess.move(fresh.opponent_move);
      chessRef.current = chess;
      sessionRef.current = fresh;
      setSession(fresh);
      setActivePlayerId(fresh.player_id);
      setPlayerId(fresh.player_id);
      setResigned(false);
      setHintPreview(null);
      setRefreshSessionId(null);
      setSyncRetrySessionId(null);
      criticalInterruptionRef.current = null;
      timedInterruptionRef.current = null;
      setCriticalInterruption(null);
      updateBoard();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not retry conversion practice.');
    } finally {
      conversionRetryPending.current = false;
      setConversionRetrying(false);
    }
  };


  const startGame = async () => {
    if (!botChoiceReady || botChoicePending || startPendingRef.current) return;
    startPendingRef.current = true;
    setStartingGame(true);
    try {
      setError(null);
      setBotPlayNotice(null);
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
      timedInterruptionRef.current = null;
      setCriticalInterruption(null);
      setSession(newSession);
      chessRef.current.reset();
      if (newSession.clock) {
        if (newSession.pgn == null) throw new Error('Missing timed game snapshot.');
        chessRef.current.loadPgn(newSession.pgn);
        setClockSnapshotAt(performance.now());
        setServerResult(newSession.result ?? null);
      } else if (newSession.opponent_move) {
        const move = newSession.opponent_move;
        chessRef.current.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
      }
      updateBoard();
      if (newSession.clock) await refreshQueuedSession(newSession.id);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Unknown error');
    } finally {
      startPendingRef.current = false;
      setStartingGame(false);
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
      if (response.status !== 'active' && !updatedSession.game_id && !updatedSession.sparring) {
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
    timedInterruptionRef.current = null;
    setCriticalInterruption(null);
    updateBoard();
  }, [updateBoard]);
  const canRefreshQueuedSession = useCallback((sessionId: string, generation: number, ownerId: string) => {
    if (refreshGenerationRef.current !== generation || sessionRef.current?.id !== sessionId) return false;
    if (getStoredPlayerId() !== ownerId || sessionRef.current.player_id !== ownerId) {
      setRefreshSessionId(sessionId);
      setError('Return to the game owner to refresh this game.');
      return false;
    }
    return true;
  }, []);

  const refreshQueuedSession = useCallback(async (
    sessionId: string, generation = ++refreshGenerationRef.current, afterClockVersion?: number,
  ) => {
    if (sessionRef.current?.id !== sessionId || refreshGenerationRef.current !== generation) return false;
    const ownerId = sessionRef.current.player_id;
    queuedMoveNeedsRefreshRef.current.add(sessionId);
    setRefreshing(true);
    updateBoard();
    if (!canRefreshQueuedSession(sessionId, generation, ownerId)) {
      setRefreshing(false);
      return false;
    }
    try {
      const refreshedSession = await (afterClockVersion === undefined
        ? ApiClient.getPlaySession(sessionId) : ApiClient.getPlaySession(sessionId, afterClockVersion));
      const receivedAt = performance.now();
      if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return false;
      if (refreshedSession.id !== sessionId || refreshedSession.player_id !== ownerId) {
        throw new Error('The refreshed game does not match its owner and session.');
      }
      const refreshedChess = new Chess();
      let result: string | null = null;
      if (!refreshedSession.game_id && chessRef.current.history().length > 0) {
        throw new Error('Missing game record.');
      }
      if (refreshedSession.clock) {
        if (refreshedSession.pgn == null) throw new Error('Missing timed game snapshot.');
        refreshedChess.loadPgn(refreshedSession.pgn);
        result = refreshedSession.result ?? null;
      } else if (refreshedSession.game_id) {
        const game = await ApiClient.getGame(refreshedSession.game_id);
        if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return false;
        refreshedChess.loadPgn(game.pgn);
        result = game.result;
      }
      chessRef.current = refreshedChess;
      sessionRef.current = refreshedSession;
      queuedMoveNeedsRefreshRef.current.delete(sessionId);
      setSession(refreshedSession);
      setClockSnapshotAt(receivedAt);
      const refreshedClock = refreshedSession.clock;
      if (refreshedSession.status === 'active' && refreshedClock?.active_color) {
        const remaining = refreshedClock.active_color === 'white'
          ? refreshedClock.white_remaining_ms : refreshedClock.black_remaining_ms;
        if (remaining > 0) refreshedDeadlines.current.delete(`${sessionId}:${refreshedClock.version}`);
      }
      setServerResult(result);
      setHintPreview(null);
      setOpponentRetry(!!refreshedSession.clock?.active_color && refreshedSession.status === 'active' &&
        refreshedSession.clock.active_color !== sessionPlayerColor(refreshedSession) &&
        failedOpponentVersions.current.has(`${sessionId}:${refreshedSession.clock.version}`));
      const timedInterruption = timedInterruptionRef.current;
      if (timedInterruption?.sessionId === sessionId && timedInterruption.playerId === ownerId) {
        const matches = refreshedSession.status === 'active' && refreshedSession.coach_mode &&
          refreshedSession.clock?.version === timedInterruption.clockVersion;
        criticalInterruptionRef.current = matches ? timedInterruption.interruption : null;
        setCriticalInterruption(criticalInterruptionRef.current);
        if (refreshedSession.status !== 'active' || !refreshedSession.coach_mode ||
          (refreshedSession.clock && refreshedSession.clock.version > timedInterruption.clockVersion)) {
          timedInterruptionRef.current = null;
        }
      } else if (refreshedSession.status !== 'active') {
        timedInterruptionRef.current = null;
        criticalInterruptionRef.current = null;
        setCriticalInterruption(null);
      }
      updateBoard();
      setSyncRetrySessionId(null);
      setRefreshSessionId(null);
      setError(null);
      return true;
    } catch (error: unknown) {
      if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return false;
      setRefreshSessionId(sessionId);
      setError(`Unable to refresh the synchronized game: ${error instanceof Error ? error.message : 'Unknown error'}`);
      return false;
    } finally {
      if (refreshGenerationRef.current === generation) setRefreshing(false);
    }
  }, [canRefreshQueuedSession, updateBoard]);

  const onClockDeadline = useCallback((version: number) => {
    const current = sessionRef.current;
    if (!current?.clock || current.clock.version !== version || current.status !== 'active') return;
    const key = `${current.id}:${current.clock.version}`;
    if (refreshedDeadlines.current.has(key)) return;
    refreshedDeadlines.current.add(key);
    void refreshQueuedSession(current.id, undefined, current.clock.version);
  }, [refreshQueuedSession]);

  const resumeOpponent = useCallback(async () => {
    const current = sessionRef.current;
    if (!current?.clock || current.status !== 'active' || !navigator.onLine ||
      current.player_id !== getStoredPlayerId() ||
      !current.clock.active_color || current.clock.active_color === sessionPlayerColor(current) ||
      opponentPendingRef.current || movePendingRef.current ||
      queuedMoveNeedsRefreshRef.current.has(current.id)) return;
    resumedClockVersions.current.add(`${current.id}:${current.clock.version}`);
    const clockVersion = current.clock.version;
    const versionKey = `${current.id}:${current.clock.version}`;
    failedOpponentVersions.current.delete(versionKey);
    opponentPendingRef.current = true;
    setOpponentPending(true);
    setOpponentRetry(false);
    setError(null);
    let failure: string | null = null;
    let conflict = false;
    try {
      await ApiClient.resumePlaySessionOpponentMove(current.id, current.clock.version);
    } catch (cause) {
      conflict = cause instanceof ApiRequestError && cause.status === 409;
      failure = cause instanceof Error ? cause.message : 'Could not resume opponent move.';
      failedOpponentVersions.current.add(versionKey);
    }
    let refreshed = await refreshQueuedSession(current.id);
    const sameOpponentTurn = () => sessionRef.current?.id === current.id &&
      sessionRef.current.player_id === current.player_id && getStoredPlayerId() === current.player_id &&
      sessionRef.current.status === 'active' && sessionRef.current.clock?.version === clockVersion &&
      sessionRef.current.clock.active_color !== null &&
      sessionRef.current.clock.active_color !== sessionPlayerColor(sessionRef.current);
    if (conflict && refreshed && sameOpponentTurn()) {
      refreshed = await refreshQueuedSession(current.id, undefined, current.clock.version);
      if (refreshed && sameOpponentTurn()) {
        setOpponentRetry(true);
        setRefreshSessionId(current.id);
        setError('The opponent move is still pending. Retry game refresh to check the saved position.');
      }
    } else if (failure && !conflict && sessionRef.current?.id === current.id &&
      sessionRef.current.player_id === current.player_id && getStoredPlayerId() === current.player_id) {
      setError(failure);
      setOpponentRetry(refreshed && sameOpponentTurn());
    }
    opponentPendingRef.current = false;
    setOpponentPending(false);
  }, [refreshQueuedSession]);

  useEffect(() => {
    if (!initialSession?.clock || initialClockRefreshed.current) return;
    initialClockRefreshed.current = true;
    void refreshQueuedSession(initialSession.id);
    return () => { initialClockRefreshed.current = false; };
  }, [initialSession, refreshQueuedSession]);

  useEffect(() => {
    if (!session?.clock || session.status !== 'active' || resuming || movePending ||
      opponentPending || !online || queuedMoveNeedsRefreshRef.current.has(session.id) ||
      !session.clock.active_color || session.clock.active_color === sessionPlayerColor(session)) return;
    if (!resumedClockVersions.current.has(`${session.id}:${session.clock.version}`)) void resumeOpponent();
  }, [session, resuming, movePending, opponentPending, online, resumeOpponent]);

  const synchronizeQueuedSession = useCallback((sessionId: string) => {
    if (sessionRef.current?.id !== sessionId || legacyDiscardPendingRef.current) return;
    if (sessionRef.current.clock) {
      void refreshQueuedSession(sessionId);
      return;
    }
    const ownerId = sessionRef.current.player_id;
    const generation = ++refreshGenerationRef.current;
    queuedMoveNeedsRefreshRef.current.add(sessionId);
    updateBoard();
    setRefreshSessionId(null);
    if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return;
    void (async () => {
      try {
        await syncQueuedMoves();
        if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return;
        const queuedMoves = await getQueuedMoves();
        if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return;
        const legacyMoves = queuedMoves.filter(
          queuedMove => queuedMove.sessionId === sessionId && typeof queuedMove.sourcePgn !== 'string',
        );
        setLegacyQueuedMoves(previous =>
          previous.length === 0 && legacyMoves.length === 0 ? previous : legacyMoves,
        );
        if (queuedMoves.some((queuedMove) => queuedMove.sessionId === sessionId)) {
          setSyncRetrySessionId(sessionId);
          setError('Queued move could not be synchronized. Reconnect to retry.');
          return;
        }
        setSyncRetrySessionId(null);
        refreshQueuedSession(sessionId, generation);
      } catch (error: unknown) {
        if (!canRefreshQueuedSession(sessionId, generation, ownerId)) return;
        setSyncRetrySessionId(sessionId);
        setError(
          `Unable to synchronize queued moves: ${
            error instanceof Error ? error.message : 'Unknown error'
          }`,
        );
      }
    })();
  }, [canRefreshQueuedSession, refreshQueuedSession, updateBoard]);

  const discardLegacyQueuedMoves = async () => {
    const confirmation = legacyDiscard;
    const current = sessionRef.current;
    if (!confirmation || !current || current.id !== confirmation.sessionId ||
      current.player_id !== confirmation.playerId || legacyDiscardPendingRef.current ||
      !navigator.onLine) return;
    if (getStoredPlayerId() !== confirmation.playerId) {
      setError('Return to the game owner before discarding queued moves.');
      return;
    }
    legacyDiscardPendingRef.current = true;
    setDiscardingLegacy(true);
    const generation = ++refreshGenerationRef.current;
    const stillCurrent = () => refreshGenerationRef.current === generation &&
      sessionRef.current?.id === confirmation.sessionId &&
      sessionRef.current.player_id === confirmation.playerId &&
      getStoredPlayerId() === confirmation.playerId;
    try {
      // Let any existing drain finish before removing only the confirmed local entries.
      await syncQueuedMoves();
      if (!stillCurrent()) return;
      const queuedMoves = await getQueuedMoves();
      if (!stillCurrent()) return;
      const confirmedLegacyMoves = queuedMoves.filter(queuedMove =>
        queuedMove.sessionId === confirmation.sessionId &&
        typeof queuedMove.sourcePgn !== 'string' &&
        confirmation.moves.some(confirmed =>
          confirmed.sessionId === queuedMove.sessionId && confirmed.move === queuedMove.move &&
          confirmed.timestamp === queuedMove.timestamp,
        ),
      );
      for (const queuedMove of confirmedLegacyMoves) {
        if (!stillCurrent()) return;
        await removeQueuedMove(queuedMove.sessionId, queuedMove.move, queuedMove.timestamp);
      }
      if (!stillCurrent()) return;
      setLegacyDiscard(null);
    } catch (cause) {
      if (stillCurrent()) setError(
        `Unable to discard queued moves: ${cause instanceof Error ? cause.message : 'Unknown error'}`,
      );
      return;
    } finally {
      legacyDiscardPendingRef.current = false;
      setDiscardingLegacy(false);
    }
    // Keep the existing move lock until remaining intents and the canonical game are checked.
    synchronizeQueuedSession(confirmation.sessionId);
  };

  useEffect(() => {
    // A resumed conversion may already contain moves beyond its source FEN.
    // Reconstruct from the persisted custom game, never from the original review.
    if (initialSession?.sparring && initialSession.status === 'active') {
      synchronizeQueuedSession(initialSession.id);
    }
  }, [initialSession, synchronizeQueuedSession]);
  const retainTimedInterruption = useCallback((source: PlaySessionRead, response: PlayMoveResponse) => {
    if (sessionRef.current?.id === source.id && sessionRef.current.player_id === source.player_id &&
      source.coach_mode && response.clock && response.critical_interruption) {
      timedInterruptionRef.current = {
        sessionId: source.id, playerId: source.player_id,
        clockVersion: response.clock.version, interruption: response.critical_interruption,
      };
    }
  }, []);


  const sourceIsCurrent = useCallback((source: AssistanceSource) => {
    const activeSession = sessionRef.current;
    const color = sessionPlayerColor(activeSession);
    return activeSession?.id === source.sessionId && activeSession.player_id === source.playerId &&
      activeSession.status === 'active' && getStoredPlayerId() === source.playerId &&
      activeSession.clock?.version === source.clockVersion &&
      (!activeSession.clock || activeSession.clock.active_color === color) && !opponentPendingRef.current &&
      chessRef.current.fen() === source.fen && positionRevision.current === source.revision &&
      (color === undefined || chessRef.current.turn() === (color === 'white' ? 'w' : 'b')) &&
      criticalInterruptionRef.current === null && !takebackPendingRef.current &&
      !queuedMoveNeedsRefreshRef.current.has(source.sessionId);
  }, []);

  const requireProtectedRefresh = useCallback((source: Pick<AssistanceSource, 'sessionId' | 'playerId'>) => {
    if (sessionRef.current?.id !== source.sessionId || sessionRef.current.player_id !== source.playerId) return;
    queuedMoveNeedsRefreshRef.current.add(source.sessionId);
    setRefreshSessionId(source.sessionId);
  }, []);

  const commitProtectedMove = useCallback(async (move: string, graded: AssistanceState) => {
    if (graded.pending || assistanceRef.current !== graded || movePendingRef.current) return;
    if (!sourceIsCurrent(graded.source) || !navigator.onLine) {
      clearAssistance();
      setAssistanceError('This checked move is no longer current. Reconnect or refresh, then check it again.');
      return;
    }
    if (!graded.result.grades.some(grade => grade.move === move)) return;
    movePendingRef.current = true;
    setMovePending(true);
    clearAssistance();
    try {
      const response = await ApiClient.makePlaySessionMove(graded.source.sessionId, {
        move, expected_generation: graded.result.generation,
        ...(graded.source.clockVersion !== undefined ? { expected_clock_version: graded.source.clockVersion } : {}),
      });
      if (graded.source.clockVersion !== undefined) {
        const current = sessionRef.current;
        if (current?.id === graded.source.sessionId && current.player_id === graded.source.playerId) {
          retainTimedInterruption(current, response);
        }
        if (current?.id !== graded.source.sessionId || current.player_id !== graded.source.playerId ||
          getStoredPlayerId() !== graded.source.playerId) {
          requireProtectedRefresh(graded.source);
          return;
        }
        await refreshQueuedSession(graded.source.sessionId);
      } else {
        if (!sourceIsCurrent(graded.source)) {
          requireProtectedRefresh(graded.source);
          return;
        }
        chessRef.current.move({ from: move.slice(0, 2), to: move.slice(2, 4), promotion: move[4] });
        flushSync(updateBoard);
        applyMoveResponse(response);
      }
    } catch (caught) {
      if (graded.source.clockVersion !== undefined) {
        await refreshQueuedSession(graded.source.sessionId);
        setAssistanceError(`${caught instanceof Error ? caught.message : 'Move could not be committed.'} No move was queued.`);
        return;
      }
      if (!sourceIsCurrent(graded.source)) {
        requireProtectedRefresh(graded.source);
        return;
      }
      // A stale generation or an uncertain commit must be reconciled, never queued or replayed.
      if (!(caught instanceof ApiRequestError) || caught.status === 409 || caught.status >= 500) {
        queuedMoveNeedsRefreshRef.current.add(graded.source.sessionId);
        setRefreshSessionId(graded.source.sessionId);
      }
      if (caught instanceof BotPlayUnavailableError) {
        // The server saved the player's move before the bot failed; the refresh shows it.
        setAssistanceError(null);
        setBotPlayNotice(caught.message);
        return;
      }
      setAssistanceError(`${caught instanceof Error ? caught.message : 'Move could not be committed.'} No move was queued. Refresh if needed, then check again.`);
    } finally {
      movePendingRef.current = false;
      setMovePending(false);
    }
  }, [applyMoveResponse, clearAssistance, refreshQueuedSession, requireProtectedRefresh, retainTimedInterruption, sourceIsCurrent, updateBoard]);

  const resumeBotTurn = async () => {
    const current = sessionRef.current;
    const color = sessionPlayerColor(current);
    if (!current || current.clock || current.status !== 'active' || !current.game_id || color === undefined ||
      chessRef.current.turn() === (color === 'white' ? 'w' : 'b') || !navigator.onLine ||
      current.player_id !== getStoredPlayerId() || movePendingRef.current || resigning ||
      takebackPendingRef.current || criticalInterruptionRef.current !== null ||
      queuedMoveNeedsRefreshRef.current.has(current.id)) return;
    const source: AssistanceSource = {
      sessionId: current.id, playerId: current.player_id,
      fen: chessRef.current.fen(), revision: positionRevision.current,
    };
    movePendingRef.current = true;
    setMovePending(true);
    clearAssistance();
    try {
      const response = await ApiClient.resumePlaySession(current.id, current.player_id);
      if (sessionRef.current?.id !== source.sessionId ||
        sessionRef.current.player_id !== source.playerId || getStoredPlayerId() !== source.playerId ||
        chessRef.current.fen() !== source.fen || positionRevision.current !== source.revision) {
        requireProtectedRefresh(source);
        return;
      }
      applyMoveResponse(response);
    } catch (caught) {
      requireProtectedRefresh(source);
      if (getStoredPlayerId() === source.playerId) {
        if (caught instanceof BotPlayUnavailableError) setBotPlayNotice(caught.message);
        else setError(`${caught instanceof Error ? caught.message : 'Bot turn failed.'} Refresh the game before retrying; your move was not replayed.`);
      }
    } finally {
      movePendingRef.current = false;
      setMovePending(false);
    }
  };

  const gradeMoves = useCallback(async (mode: PlayAssistanceMode, moves: string[]) => {
    const activeSession = sessionRef.current;
    if (!activeSession || movePendingRef.current || opponentPendingRef.current || assistanceRef.current?.pending || resigning ||
      takebackPendingRef.current || moves.length < 1 || moves.length > 3) return;
    const source: AssistanceSource = {
      sessionId: activeSession.id, playerId: activeSession.player_id,
      fen: chessRef.current.fen(), revision: positionRevision.current, clockVersion: activeSession.clock?.version,
    };
    clearAssistance();
    if (!navigator.onLine || !sourceIsCurrent(source)) {
      setAssistanceError('Move checking requires your active game, your turn, and a connection. No move was played.');
      return;
    }
    const request = ++assistanceRequest.current;
    const pending: AssistanceState = { source, mode, pending: true };
    assistanceRef.current = pending;
    setAssistance(pending);
    setHintPreview(null);
    try {
      const result = await ApiClient.gradePlayMoves(source.sessionId, {
        player_id: source.playerId, fen: source.fen, moves, mode,
      });
      // Cancellation hides the verdict, not assistance already durably recorded for this position.
      const current = sessionRef.current;
      if (current?.id === source.sessionId && current.player_id === source.playerId &&
        getStoredPlayerId() === source.playerId && result.fen === source.fen && result.mode === mode) {
        const updated = { ...current, assistance_modes: [...new Set([...current.assistance_modes, ...result.assistance_modes])] };
        sessionRef.current = updated;
        setSession(updated);
      }
      if (request !== assistanceRequest.current) return;
      if (!sourceIsCurrent(source) || !navigator.onLine || result.fen !== source.fen || result.mode !== mode) {
        clearAssistance();
        setAssistanceError('The game changed while checking. No move was played. Check the current position again.');
        return;
      }
      if (!Number.isSafeInteger(result.generation) || result.generation < 0 ||
        result.grades.length !== moves.length || result.grades.some((grade, index) =>
          grade.move !== moves[index] || !['sound', 'inaccuracy', 'mistake', 'blunder'].includes(grade.band))) {
        throw new Error('The engine did not return complete scored move grades. Check again.');
      }
      const graded: AssistanceState = { source, mode, pending: false, result };
      assistanceRef.current = graded;
      setAssistance(graded);
      if (mode === 'guard' && result.grades[0].band !== 'blunder') {
        await commitProtectedMove(moves[0], graded);
      }
    } catch (caught) {
      if (request !== assistanceRequest.current) return;
      clearAssistance();
      if (caught instanceof ApiRequestError && caught.status === 409) {
        queuedMoveNeedsRefreshRef.current.add(source.sessionId);
        setRefreshSessionId(source.sessionId);
      }
      setAssistanceError(caught instanceof ApiRequestError && caught.status === 503
        ? 'The engine is busy or could not grade this position. No move was played. Try checking again.'
        : `${caught instanceof Error ? caught.message : 'Move checking failed.'} No move was played.`);
    }
  }, [clearAssistance, commitProtectedMove, resigning, sourceIsCurrent]);

  const handleMove = useCallback(async (lan: string) => {
    const activeSession = sessionRef.current;
    const color = sessionPlayerColor(activeSession);
    if (!activeSession || activeSession.status !== 'active' || takebackPendingRef.current || movePendingRef.current || opponentPendingRef.current || assistanceRef.current !== null ||
      (activeSession.clock && (!navigator.onLine || activeSession.clock.active_color !== color)) ||
      (color !== undefined && chessRef.current.turn() !== (color === 'white' ? 'w' : 'b')) ||
      criticalInterruptionRef.current !== null ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
    if (guardEnabledRef.current) {
      void gradeMoves('guard', [lan]);
      return;
    }
    clearAssistance();
    movePendingRef.current = true;
    let moveApplied = false;
    setMovePending(true);
    const sourcePgn = chessRef.current.pgn();
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
          retainTimedInterruption(activeSession, response);
          if (sessionRef.current?.id !== activeSession.id ||
            sessionRef.current.player_id !== activeSession.player_id || getStoredPlayerId() !== activeSession.player_id) {
            requireProtectedRefresh({
              sessionId: activeSession.id, playerId: activeSession.player_id,
            });
            return;
          }
          await refreshQueuedSession(activeSession.id);
        } else applyMoveResponse(response);
      } catch (error: unknown) {
        if (!activeSession.clock && !navigator.onLine) {
          await queueMove(activeSession.id, lan, sourcePgn);
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
        if (moveApplied && sessionRef.current?.id === activeSession.id &&
          (!(error instanceof ApiRequestError) || error.status === 409 || error.status >= 500)) {
          queuedMoveNeedsRefreshRef.current.add(activeSession.id);
          setRefreshSessionId(activeSession.id);
        }
        updateBoard();
      }
      setError(error instanceof Error ? error.message : 'Unknown error');
    } finally {
      movePendingRef.current = false;
      setMovePending(false);
    }
  }, [applyMoveResponse, clearAssistance, gradeMoves, refreshQueuedSession, requireProtectedRefresh, retainTimedInterruption, updateBoard]);

  const takeback = async () => {
    const activeSession = sessionRef.current;
    if (!activeSession || activeSession.status !== 'active' || activeSession.coach_mode || activeSession.clock ||
      movePending || resigning || assistanceRef.current?.pending || takebackPendingRef.current || !navigator.onLine ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id) ||
      chessRef.current.history().length < 2) return;
    clearAssistance();
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
    if (!activeSession || activeSession.status !== 'active' || movePending || resigning || assistanceRef.current?.pending || takebackPendingRef.current ||
      queuedMoveNeedsRefreshRef.current.has(activeSession.id)) return;
    clearAssistance();
    setResigning(true);
    try {
      const endedSession = await ApiClient.resignPlaySession(activeSession.id);
      if (endedSession.clock) {
        if (endedSession.pgn == null) throw new Error('Missing timed game snapshot.');
        const endedChess = new Chess();
        endedChess.loadPgn(endedSession.pgn);
        chessRef.current = endedChess;
        setClockSnapshotAt(performance.now());
        setServerResult(endedSession.result ?? null);
        updateBoard();
      }
      sessionRef.current = endedSession;
      setSession(endedSession);
      if (activeSession.clock) await refreshQueuedSession(activeSession.id);
      setResigned(true);
      criticalInterruptionRef.current = null;
      timedInterruptionRef.current = null;
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

    const activeSessionId = restoreRetry?.sessionId || requestedSessionId || localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
    if (!activeSessionId) {
      setResuming(false);
      return;
    }

    setResuming(true);
    setRefreshSessionId(null);
    setError(null);
    let cancelled = false;
    let sessionResolved = false;
    void (async () => {
      try {
        const resumedSession = await ApiClient.getPlaySession(activeSessionId);
        const receivedAt = performance.now();
        sessionResolved = true;
        if (cancelled || sessionRef.current) return;
        if (resumedSession.status !== 'active' && !resumedSession.sparring &&
          (!resumedSession.clock || (requestedSessionId === activeSessionId && preservedSessionId.current))) {
          if (localStorage.getItem(ACTIVE_PLAY_SESSION_STORAGE_KEY) === activeSessionId) {
            localStorage.removeItem(ACTIVE_PLAY_SESSION_STORAGE_KEY);
          }
          setRestoreRetry(null);
          if (requestedSessionId) consumeRequestedSession();
          return;
        }

        const resumedChess = new Chess();
        let result: string | null = null;
        if (resumedSession.clock) {
          if (resumedSession.pgn == null) throw new Error('Missing timed game snapshot.');
          resumedChess.loadPgn(resumedSession.pgn);
          result = resumedSession.result ?? null;
        } else if (resumedSession.game_id) {
          const game = await ApiClient.getGame(resumedSession.game_id);
          if (cancelled || sessionRef.current) return;
          result = game.result;
          resumedChess.loadPgn(game.pgn);
        }
        let hasQueuedMove = false;
        let queuedMoveError: string | null = null;
        if (resumedSession.status === 'active' && !resumedSession.clock) {
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
        if (resumedSession.sparring) {
          setPlayerId(resumedSession.player_id);
          setBotLevel(resumedSession.opponent_config.bot_level ?? DEFAULT_BOT_LEVEL);
        }
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
          setRestoreRetry(null);
          if (requestedSessionId) consumeRequestedSession();
          setError('Previous game is no longer available.');
          return;
        }
        setRefreshSessionId(activeSessionId);
        setError(`Unable to resume game: ${error instanceof Error ? error.message : 'Unknown error'}`);
      } finally {
        if (!cancelled) setResuming(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [initialSession, requestedSessionId, restoreRetry, consumeRequestedSession, synchronizeQueuedSession, updateBoard]);

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
      const { queuedMove, response, botPlayUnavailable } =
        (event as CustomEvent<QueuedMoveSyncSuccess>).detail;
      const currentSession = sessionRef.current;
      if (queuedMove.sessionId !== currentSession?.id) return;
      if (currentSession.clock) return;
      queuedMoveNeedsRefreshRef.current.add(queuedMove.sessionId);
      if (response !== null) applyMoveResponse(response);
      if (botPlayUnavailable !== undefined) setBotPlayNotice(botPlayUnavailable);
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


  const assistanceBlocked = movePending || opponentPending || resigning || takingBack || !online ||
    criticalInterruption !== null || !session || session.player_id !== getStoredPlayerId() ||
    (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
      (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
    queuedMoveNeedsRefreshRef.current.has(session.id);
  const botTurnPending = !session?.clock && session?.status === 'active' && sessionPlayerColor(session) !== undefined &&
    session.player_id === getStoredPlayerId() && chessRef.current.turn() !==
      (sessionPlayerColor(session) === 'white' ? 'w' : 'b') &&
    !queuedMoveNeedsRefreshRef.current.has(session.id);

  return (
    <div className="play-screen working-screen" data-testid="play-screen">
      <h1 ref={heading} tabIndex={-1}>{session?.sparring ? 'Convert the win — conversion practice' : 'Play against Scan64'}</h1>
      <div className="visually-hidden" role="status" aria-label="Game position">{sessionAnnouncement}</div>
      {resuming ? <div role="status" data-testid="resuming-game">Resuming game...</div> : null}
      <div className={session ? 'board-workspace' : 'play-setup'}>
      {session && <div className="board-workspace__board">
      <CoachBoard
        fen={boardPosition.fen} pgn={boardPosition.pgn}
        {...(currentHint ? { fen: currentHint.fen, pgn: currentHint.fen === boardPosition.fen ? boardPosition.pgn : undefined, visualizations: currentHint.visualizations } : {})}
        testId="chessground-board"
        preview={currentHint !== null && currentHint.fen !== boardPosition.fen}
        orientation={sessionPlayerColor(session) ?? 'white'}
        flippable gameEnded={session.status !== 'active'}
        deferMoveToParent={guardEnabled}
        premoveColor={online && session.status === 'active' && !resigning && !takingBack &&
          criticalInterruption === null && !currentHint && assistance === null && (!!session.clock || !queuedMoveNeedsRefreshRef.current.has(session.id))
          ? sessionPlayerColor(session) : undefined}
        disabled={session.status !== 'active' || movePending || opponentPending || resigning || takingBack || assistance !== null ||
          (!!session.clock && (!online || session.clock.active_color !== sessionPlayerColor(session))) ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          criticalInterruption !== null || (currentHint !== null && currentHint.fen !== boardPosition.fen) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)}
        onMove={handleMove}
      />
      </div>}
      <div className="board-workspace__side">
      {!session && !resuming && !refreshSessionId && (
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
          <button className="btn btn--primary" onClick={startGame} disabled={!botChoiceReady || botChoicePending || startingGame} data-testid="start-btn">Start Game</button>
        </Panel>
      )}
      {botPlayNotice && <div className="error" role="alert">{botPlayNotice}</div>}
      {error && <div className="error" role="alert">{error}</div>}
      {syncRetrySessionId ? (
        <>
          <button
            type="button"
            data-testid="retry-move-sync"
            disabled={discardingLegacy || legacyDiscard?.sessionId === syncRetrySessionId}
            onClick={() => synchronizeQueuedSession(syncRetrySessionId)}
          >
            Retry move synchronization
          </button>
          {session && legacyQueuedMoves.some(move => move.sessionId === session.id) && (
            <button type="button" disabled={discardingLegacy || !online}
              onClick={() => setLegacyDiscard({
                sessionId: session.id, playerId: session.player_id, moves: legacyQueuedMoves,
              })}>
              Discard legacy queued moves…
            </button>
          )}
        </>
      ) : null}
      {legacyDiscard && legacyDiscard.sessionId === session?.id && (
        <div role="region" aria-label="Confirm legacy move discard" aria-busy={discardingLegacy}>
          <p>These older queued moves have no recorded source position and cannot be replayed safely.
            Discard only the selected local moves and reload the saved game? Unsent moves will be lost.
            Saved games and moves with a recorded source are kept.</p>
          <button type="button" disabled={discardingLegacy || !online}
            onClick={() => void discardLegacyQueuedMoves()}>
            {discardingLegacy ? 'Discarding…' : 'Discard and refresh'}
          </button>
          <button type="button" disabled={discardingLegacy} onClick={() => setLegacyDiscard(null)}>
            Keep queued moves
          </button>
        </div>
      )}
      {refreshSessionId ? (
        <button
          type="button"
          data-testid="retry-game-refresh"
          onClick={() => {
            if (sessionRef.current) synchronizeQueuedSession(refreshSessionId);
            else setRestoreRetry({ sessionId: refreshSessionId });
          }}
        >
          Retry game refresh
        </button>
      ) : null}
      {botTurnPending && <div role="status">
        <p>{movePending ? 'Waiting for the bot…' : 'Your move is saved. Resume the bot turn without replaying it.'}</p>
        <button type="button" onClick={() => void resumeBotTurn()}
          disabled={movePending || resigning || takingBack || !online || criticalInterruption !== null}>
          {movePending ? 'Waiting for bot…' : 'Resume bot turn'}
        </button>
      </div>}
      {session && <Panel title="Current game">
        <p>You play {sessionPlayerColor(session) ?? 'your chosen side'} against Scan64.</p>
        <p data-testid="session-info">Status: {session.status}</p>
        {session.sparring && <p>Source game {session.sparring.source_game_id}, ply {session.sparring.source_ply}.
          {' '}Your original side is preserved. Custom starts do not earn ordinary strength credit.</p>}
        {session.sparring && <p>Stored analysis {session.sparring.source_analysis_id}
          {' · '}Practice session {session.id} · seed {session.sparring.seed}</p>}
        {session.assistance_modes.length > 0 ? <p data-testid="play-assistance-used">
          Engine assistance used: {session.assistance_modes.map(mode =>
            mode === 'guard' ? 'blunder guard' : mode === 'candidates' ? 'Candidates' : 'hints').join(', ')}.
          {' '}Recorded permanently, including after takeback or reload. Assisted positions do not earn good-move skill credit.
        </p> : null}
      </Panel>}
      {session?.clock ? <PlayClocks key={`${session.id}:${session.clock.version}:${session.clock.server_now}`}
        clock={session.clock} snapshotAt={clockSnapshotAt} onDeadline={onClockDeadline} /> : null}
      {session?.clock && !online ? <p role="status">Timed moves require a connection and are never queued offline. The clock keeps running.</p> : null}
      {opponentPending ? <p role="status">Opponent thinking…</p> : null}
      {session?.clock && refreshing ? <p role="status">Refreshing game…</p> : null}
      {opponentRetry && !opponentPending && session?.status === 'active' ? <button type="button"
        disabled={!online || queuedMoveNeedsRefreshRef.current.has(session.id)}
        onClick={() => void resumeOpponent()}>Retry opponent move</button> : null}
      {session?.status === 'active' && <Panel title="Move assistance" aria-label="Move assistance">
        <p>Candidates and blunder guard use full engine assistance. Checking is recorded before results appear, even if you cancel.</p>
        <label>
          <input ref={guardToggle} type="checkbox" checked={guardEnabled} disabled={movePending || resigning || takingBack}
            onChange={event => { clearAssistance(); setGuardEnabled(event.target.checked); }} />
          Blunder guard
        </label>
        <p>Off by default. When enabled, your move is checked before it is played. Blunders need your confirmation.</p>
        {!online ? <p role="status">Move assistance is online-only. Turn off blunder guard to use ordinary offline play.</p> : null}
        <label>Add candidate move
          <select aria-label="Add candidate move" value="" disabled={assistanceBlocked || assistance?.pending || candidateMoves.length >= 3}
            onChange={event => {
              const move = event.target.value;
              if (!move || candidateMoves.includes(move) || candidateMoves.length >= 3) return;
              clearAssistance();
              setCandidateMoves(previous => [...previous, move]);
            }}>
            <option value="">Choose a legal move…</option>
            {legalCandidates.filter(candidate => !candidateMoves.includes(candidate.move)).map(candidate =>
              <option key={candidate.move} value={candidate.move}>{candidate.san}</option>)}
          </select>
        </label>
        <p>Select one to three legal moves. The board stays unchanged until you choose Play.</p>
        {candidateMoves.length > 0 ? <ul aria-label="Selected candidates">
          {candidateMoves.map(move => <li key={move}>
            {legalCandidates.find(candidate => candidate.move === move)?.san}
            {' '}<button type="button" disabled={movePending || assistance?.pending}
              onClick={() => { clearAssistance(); setCandidateMoves(previous => previous.filter(value => value !== move)); }}>
              Remove {legalCandidates.find(candidate => candidate.move === move)?.san}
            </button>
          </li>)}
        </ul> : null}
        <button type="button" disabled={assistanceBlocked || assistance?.pending || candidateMoves.length === 0}
          onClick={() => void gradeMoves('candidates', candidateMoves)}>Grade candidates</button>
        {candidateMoves.length > 0 || assistance ? <button type="button" disabled={movePending}
          onClick={() => { clearAssistance(); setCandidateMoves([]); }}>
          {assistance?.pending ? 'Cancel checking' : 'Clear candidates and checks'}
        </button> : null}
        {assistance?.pending ? <p role="status" aria-live="polite">
          {assistance.mode === 'guard' ? 'Checking your move…' : 'Grading candidates…'} No move has been played.
        </p> : null}
        {movePending ? <p role="status">Playing move…</p> : null}
        {assistanceError ? <p role="alert">{assistanceError}</p> : null}
        {assistance && !assistance.pending && assistance.mode === 'candidates' ? <ul aria-label="Candidate grades">
          {assistance.result.grades.map(grade => <li key={grade.move}>
            <strong>{grade.san}: {grade.band}</strong>
            {' '}({Math.round(grade.win_probability_loss * 100)} percentage points of winning chance lost)
            {' '}<button type="button" disabled={assistanceBlocked}
              onClick={() => void commitProtectedMove(grade.move, assistance)}>Play {grade.san}</button>
          </li>)}
        </ul> : null}
        {assistance && !assistance.pending && assistance.mode === 'guard' ? <div role="alertdialog"
          aria-label="Blunder warning" aria-describedby="play-blunder-warning" onKeyDown={event => {
            if (event.key === 'Escape') { clearAssistance(); guardToggle.current?.focus(); }
          }}>
          <p id="play-blunder-warning"><strong>{assistance.result.grades[0].san} is a blunder.</strong>
            {' '}Your original position is unchanged. Cancel to choose another move, or explicitly play this move anyway.</p>
          <button ref={guardCancel} type="button" onClick={() => { clearAssistance(); guardToggle.current?.focus(); }}>Cancel</button>
          <button type="button" disabled={assistanceBlocked}
            onClick={() => void commitProtectedMove(assistance.result.grades[0].move, assistance)}>Play anyway</button>
        </div> : null}
      </Panel>}
      {session?.status === 'active' && <PlayHealth playerId={session.player_id} sessionId={session.id}
        fen={boardPosition.fen} previousFen={boardPosition.previousFen} learner={sessionPlayerColor(session)}
        learnerToMove={sessionPlayerColor(session) !== undefined && chessRef.current.turn() ===
          (sessionPlayerColor(session) === 'white' ? 'w' : 'b')}
        onAssistance={handleHealthAssistance} />}
      {session?.status === 'active' && <HintPanel playerId={session.player_id} sessionId={session.id}
        fen={boardPosition.fen} onPreview={handleHintPreview}
        disabled={movePending || opponentPending || resigning || takingBack || assistance !== null || criticalInterruption !== null ||
          (sessionPlayerColor(session) !== undefined && chessRef.current.turn() !==
            (sessionPlayerColor(session) === 'white' ? 'w' : 'b')) ||
          queuedMoveNeedsRefreshRef.current.has(session.id)} />}
      {session?.status === 'active' && <button type="button" onClick={() => void resign()}
        disabled={movePending || resigning || takingBack || assistance?.pending || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {resigning ? 'Resigning…' : 'Resign'}
      </button>}
      {session?.status === 'active' && !session.coach_mode && <button type="button"
        data-testid="takeback-btn" onClick={() => void takeback()}
        disabled={!!session.clock || movePending || resigning || takingBack || assistance?.pending || !navigator.onLine ||
          chessRef.current.history().length < 2 || queuedMoveNeedsRefreshRef.current.has(session.id)}>
        {takingBack ? 'Taking back…' : 'Take back'}
      </button>}
      {session?.clock && session.status === 'active' ? <p>Takebacks are unavailable in timed games.</p> : null}
      {session && session.status !== 'active' && <div role={resigned ? 'status' : undefined} data-testid="game-result">
        {session.sparring ? <section aria-label="Conversion result">
          {conversionRefreshing && <p role="status">Loading recorded conversion result…</p>}
          {conversionRefreshFailed && <button type="button" onClick={() => void refreshConversionResult(session.id)}>Retry conversion result</button>}
          {!conversionRefreshing && !conversionRefreshFailed && <>
            <p>{session.sparring.verification_status === 'historical' ? 'Historical result (not verified)' : 'Verified conversion practice'}
              {' · '}{session.sparring.outcome ?? 'Result not recorded'}{session.sparring.result ? ` · ${session.sparring.result}` : ''}</p>
            {session.sparring.completed_at && <p>Completed: <time dateTime={session.sparring.completed_at}>{session.sparring.completed_at}</time></p>}
            {session.sparring.next_review_at && <p>Next review: <time dateTime={session.sparring.next_review_at}>{session.sparring.next_review_at}</time></p>}
          </>}
          {(session.sparring.outcome !== 'win' || session.sparring.verification_status === 'historical') && <>
            <label>Conversion bot level
              <select value={botLevel} disabled={conversionRetrying || conversionRefreshing || conversionRefreshFailed}
                onChange={event => setBotLevel(event.target.value)}>
                {bots.map(bot => <option key={bot.level} value={String(bot.level)}>{bot.level}. {bot.name}</option>)}
              </select>
            </label>
            <button type="button" onClick={() => void retryConversion()}
              disabled={conversionRetrying || conversionRefreshing || conversionRefreshFailed || bots.length === 0}>
              {conversionRetrying ? 'Starting...' : 'Retry conversion practice'}
            </button>
          </>}
        </section> : session.clock ? <>
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
