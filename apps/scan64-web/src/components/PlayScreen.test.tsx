import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { StrictMode } from 'react';
import { PlayScreen } from './PlayScreen';
import { ApiClient, ApiRequestError, ensurePlayerAuthorization, setActivePlayerId } from '../api/client';
import type { GameRead, LessonSpec, PlayAssistanceRead, PlayClockRead, PlayMoveResponse, PlaySessionRead, PlayerRead } from '../api/types';
import { Chess } from 'chess.js';
import { onboardingFixture } from './strength.test-fixtures';
import {
  getQueuedMoves,
  QUEUED_MOVE_SYNC_SUCCEEDED,
  queueMove,
  removeQueuedMove,
  syncQueuedMoves,
  type QueuedMove,
} from '../api/offlineQueue';

type MoveHandler = (orig: string, dest: string) => void | Promise<void>;

const chessgroundMock = vi.hoisted(() => ({
  after: undefined as MoveHandler | undefined,
  color: undefined as 'white' | 'black' | undefined,
  set: vi.fn(),
  premoveSet: undefined as ((from: string, to: string) => void) | undefined,
  state: {
    premovable: { current: undefined as [string, string] | undefined },
    drawable: { shapes: [] },
  },
  cancelPremove: vi.fn(),
  redrawAll: vi.fn(),
  destroy: vi.fn(),
}));

function getRegisteredMoveHandler(): MoveHandler {
  if (!chessgroundMock.after) {
    throw new Error('Chessground move handler was not registered');
  }
  return chessgroundMock.after;
}

vi.mock('chessground', () => ({
  Chessground: (
    _element: Element,
    config: {
      movable: {
        color?: 'white' | 'black';
        events?: { after?: MoveHandler };
      };
      premovable?: { events?: { set?: (from: string, to: string) => void } };
    }
  ) => {
    chessgroundMock.after = config.movable.events?.after;
    chessgroundMock.color = config.movable.color;
    chessgroundMock.premoveSet = config.premovable?.events?.set;
    return { set: chessgroundMock.set, redrawAll: chessgroundMock.redrawAll, destroy: chessgroundMock.destroy,
      state: chessgroundMock.state, cancelPremove: chessgroundMock.cancelPremove };
  },
}));

vi.mock('../api/offlineQueue', () => ({
  getQueuedMoves: vi.fn(),
  syncQueuedMoves: vi.fn(),
  queueMove: vi.fn(),
  removeQueuedMove: vi.fn(),
  QUEUED_MOVE_SYNC_FAILED: 'scan64-queued-move-sync-failed',
  QUEUED_MOVE_SYNC_SUCCEEDED: 'scan64-queued-move-sync-succeeded',
}));

vi.mock('./CriticalMomentReview', () => ({
  CriticalMomentReview: ({
    lesson,
    opportunityId,
    sessionId,
    onComplete,
  }: {
    lesson: LessonSpec;
    opportunityId: string;
    sessionId: string;
    onComplete?: () => void;
  }) => (
    <div
      data-testid="critical-moment-review"
      data-lesson-id={lesson.lesson_id}
      data-opportunity-id={opportunityId}
      data-study-session-id={sessionId}
    >
      <button type="button" data-testid="complete-critical-review" onClick={onComplete}>
        Finish Review
      </button>
    </div>
  ),
}));

const serverLesson: LessonSpec = {
  schema_version: '0.2.0',
  lesson_id: 'lesson-123',
  source: { kind: 'player_game', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
  diagnosis: { primary: 'test', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'test', instruction: 'Find the best move' },
  interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
  hints: [],
  explanation: { text: 'Server-generated lesson' },
  verification: {
    status: 'verified',
    engine: 'stockfish',
    engine_binary_digest: 'digest',
    nodes: 1,
    multipv: 1,
    verified_at: '2026-07-31T00:00:00Z',
  },
  mastery: { skill_key: 'board_awareness.hanging_piece', delta: 0.1 },
};

// Mock the API client
vi.mock('../api/client', () => ({
  ApiRequestError: class ApiRequestError extends Error {
    readonly status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
  BotPlayUnavailableError: class BotPlayUnavailableError extends Error {
    readonly status = 503;
    readonly reason: string | null;

    constructor(reason: string | null) {
      super('Bot play is unavailable under the active engine profile. Switch to the Stockfish profile to play the bot.');
      this.reason = reason;
    }
  },
  ApiClient: {
    getBots: vi.fn().mockResolvedValue([{ level: 1, name: 'Server beginner', description: 'Friendly bot' }]),
    getPlayerOnboarding: vi.fn(),
    setPlayerBotChoice: vi.fn(),
    takebackPlaySession: vi.fn(),
    createPlayer: vi.fn(),
    createGame: vi.fn(),
    createPlaySession: vi.fn(),
    makePlaySessionMove: vi.fn(),
    resumePlaySession: vi.fn(),
    gradePlayMoves: vi.fn(),
    resignPlaySession: vi.fn(),
    resumePlaySessionOpponentMove: vi.fn(),
    getGame: vi.fn(),
    getPlaySession: vi.fn(),
    getTrainingSession: vi.fn().mockResolvedValue({ session_id: 'study-1', lessons: [], move_choices: {}, attempts_used: {} }),
    recordLessonAttempt: vi.fn(),
  },
  setActivePlayerId: vi.fn(),
  ensurePlayerAuthorization: vi.fn(async (playerId: string) => playerId),
  getStoredPlayerId: () => localStorage.getItem('scan64_player_id'),
}));


const timedClock: PlayClockRead = {
  initial_seconds: 180, increment_seconds: 2, white_remaining_ms: 180000, black_remaining_ms: 180000,
  active_color: 'white', deadline_at: '2026-11-19T12:03:00Z', server_now: '2026-11-19T12:00:00Z',
  version: 4, terminal_reason: null, flagged_color: null,
};
const timedSession: PlaySessionRead = {
  id: 'timed-session', player_id: 'test-player', game_id: 'timed-game', player_color: 'white',
  opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [], clock: timedClock,
  pgn: '', result: '*',
};
const timedGame = { id: 'timed-game', pgn: '', white: 'test-player', black: 'Stockfish', result: '*' };

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const assistanceSession: PlaySessionRead = {
  id: 'assisted-game', player_id: 'test-player', game_id: 'game-123',
  opponent_config: {}, player_color: 'white', status: 'active', coach_mode: false, assistance_modes: [],
};
const startingFen = new Chess().fen();
const blunderVerdict: PlayAssistanceRead = {
  fen: startingFen, generation: 7, mode: 'guard', assistance_modes: ['guard'],
  grades: [{ move: 'e2e4', san: 'e4', band: 'blunder', win_probability_loss: 0.35 }],
};

describe('PlayScreen', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/play');
    vi.resetAllMocks();
    vi.mocked(ensurePlayerAuthorization).mockImplementation(async playerId => playerId);
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(onboardingFixture);
    vi.mocked(ApiClient.setPlayerBotChoice).mockImplementation(async (_playerId, botLevel) => ({
      ...onboardingFixture, manual_bot_level: botLevel, preferred_bot_level: botLevel,
    }));
    vi.mocked(ApiClient.getBots).mockResolvedValue([
      { level: 1, name: 'Server beginner', description: 'Friendly bot' },
      { level: 8, name: 'Server expert', description: 'Expert bot' },
    ]);
    localStorage.clear();
    vi.mocked(setActivePlayerId).mockImplementation(playerId => {
      localStorage.setItem('scan64_player_id', playerId);
    });
    chessgroundMock.after = undefined;
    chessgroundMock.premoveSet = undefined;
    chessgroundMock.state.premovable.current = undefined;
    chessgroundMock.cancelPremove.mockReset();
    chessgroundMock.cancelPremove.mockImplementation(() => { chessgroundMock.state.premovable.current = undefined; });
    chessgroundMock.color = undefined;
    chessgroundMock.set.mockReset();
    chessgroundMock.redrawAll.mockReset();
    chessgroundMock.destroy.mockReset();
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({ session_id: 'study-1', lessons: [], move_choices: {}, attempts_used: {} });
    vi.mocked(getQueuedMoves).mockResolvedValue([]);
  });

  it('installs board, turn and clock from one DTO even if another owner request advances the game', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const snapshot = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(snapshot);
    // A legacy second request would observe a later board with Black to move,
    // but retain version 6 / White's clock and stall until its deadline.
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 2. Nf3 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Stale clock version', 409));
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => {
      expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
        fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
      }));
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    });
    expect(ApiClient.getPlaySession).toHaveBeenCalledExactlyOnceWith(timedSession.id);
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active');
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('timed-session', { move: 'g1f3', expected_clock_version: 6 });
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
  });

  it.each(['snapshot', 'failure'] as const)('restores timed initial sessions during StrictMode replay and rejects a late %s', async staleOutcome => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const abandoned = Promise.withResolvers<PlaySessionRead>();
    const current = Promise.withResolvers<PlaySessionRead>();
    const fresh = { ...timedSession, pgn: '1. d4 d5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockReturnValueOnce(abandoned.promise)
      .mockReturnValueOnce(current.promise);
    render(<StrictMode><PlayScreen initialSession={timedSession} /></StrictMode>);
    await act(async () => {});
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    await act(async () => { current.resolve(fresh); });
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('d5');
    await act(async () => {
      if (staleOutcome === 'snapshot') {
        abandoned.resolve({ ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 8 } });
      } else {
        abandoned.reject(new Error('Abandoned refresh failed'));
      }
    });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('d5');
    expect(screen.getByTestId('chessground-board-san-move-list')).not.toHaveTextContent('e5');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('offers only supported clock presets and posts a numeric control', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({ id: 'test-player', preferences: {} });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(timedSession);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(timedSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue(timedGame);
    render(<PlayScreen />);
    expect(screen.getByRole('combobox', { name: 'Time control' })).toHaveValue('0');
    expect(Array.from(screen.getByRole('combobox', { name: 'Time control' }).children).map(option => option.textContent))
      .toEqual(['Untimed', '1+0', '3+2', '5+0', '10+5']);
    fireEvent.change(screen.getByRole('combobox', { name: 'Time control' }), { target: { value: '2' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByRole('region', { name: 'Game clocks' });
    expect(ApiClient.createPlaySession).toHaveBeenCalledWith(expect.objectContaining({
      clock_config: { initial_seconds: 180, increment_seconds: 2 },
    }));
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    expect(screen.getByText('Takebacks are unavailable in timed games.')).toBeInTheDocument();
  });

  it('guards repeated starts synchronously and disables Start Game throughout player and game creation', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const player = Promise.withResolvers<PlayerRead>();
    const game = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.createPlayer).mockReturnValue(player.promise);
    vi.mocked(ApiClient.createPlaySession).mockReturnValue(game.promise);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(timedSession);
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    act(() => {
      fireEvent.click(screen.getByTestId('start-btn'));
      fireEvent.click(screen.getByTestId('start-btn'));
    });
    expect(screen.getByTestId('start-btn')).toBeDisabled();
    expect(ApiClient.createPlayer).toHaveBeenCalledTimes(1);
    expect(ApiClient.createPlaySession).not.toHaveBeenCalled();
    await act(async () => { player.resolve({ id: 'test-player', preferences: {} }); });
    expect(screen.getByTestId('start-btn')).toBeDisabled();
    fireEvent.click(screen.getByTestId('start-btn'));
    expect(ApiClient.createPlaySession).toHaveBeenCalledTimes(1);
    await act(async () => { game.resolve(timedSession); });
    expect(screen.getByTestId('session-info')).toHaveTextContent('active');
    expect(ApiClient.createPlayer).toHaveBeenCalledTimes(1);
    expect(ApiClient.createPlaySession).toHaveBeenCalledTimes(1);
  });

  it.each(['player', 'session'] as const)('allows a fresh start after %s creation fails', async stage => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({ id: 'test-player', preferences: {} });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(timedSession);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(timedSession);
    if (stage === 'player') vi.mocked(ApiClient.createPlayer).mockRejectedValueOnce(new Error('Start unavailable'));
    else vi.mocked(ApiClient.createPlaySession).mockRejectedValueOnce(new Error('Start unavailable'));
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Start unavailable');
    expect(screen.getByTestId('start-btn')).toBeEnabled();
    fireEvent.click(screen.getByTestId('start-btn'));
    expect(await screen.findByTestId('session-info')).toHaveTextContent('active');
    expect(ApiClient.createPlayer).toHaveBeenCalledTimes(2);
    expect(ApiClient.createPlaySession).toHaveBeenCalledTimes(stage === 'player' ? 1 : 2);
  });

  it.each([false, true])('starts a durable bot-first timed turn and exposes a failed command retry: %s', async (failsFirst) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const pending = { ...timedSession, player_color: 'black' as const, opponent_move: null,
      clock: { ...timedClock, version: 0 } };
    const after = { ...pending, opponent_move: 'e2e4', pgn: '1. e4 *',
      clock: { ...timedClock, version: 1, active_color: 'black' as const } };
    const opponent = Promise.withResolvers<PlayMoveResponse>();
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(pending);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(pending);
    vi.mocked(ApiClient.getGame).mockResolvedValue(timedGame);
    if (failsFirst) {
      vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockRejectedValueOnce(new Error('Engine unavailable'));
    }
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockReturnValue(opponent.promise);
    render(<PlayScreen />);
    fireEvent.change(screen.getByTestId('player-color-select'), { target: { value: 'black' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Time control' }), { target: { value: '2' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));
    await waitFor(() => expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledWith('timed-session', 0));
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(1);
    if (failsFirst) {
      const retry = await screen.findByRole('button', { name: 'Retry opponent move' });
      expect(screen.getByRole('alert')).toHaveTextContent('Engine unavailable');
      fireEvent.click(retry);
      await waitFor(() => expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(2));
    }
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 *' });
    await act(async () => { opponent.resolve({ opponent_move: 'e2e4', status: after.status, clock: after.clock }); });
    await waitFor(() => expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active'));
    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    }));
    expect(screen.queryByRole('button', { name: 'Retry opponent move' })).not.toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('uses authoritative increments and the latest exact clock version for each human move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6, white_remaining_ms: 181000, black_remaining_ms: 179000 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active', clock: after.clock });
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveTextContent('3:01'));
    expect(screen.getByTestId('black-clock')).toHaveTextContent('2:59');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('timed-session', { move: 'e2e4', expected_clock_version: 4 });
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...after, pgn: '1. e4 e5 2. Nf3 Nc6 *', clock: { ...after.clock, version: 8 } });
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenLastCalledWith('timed-session', { move: 'g1f3', expected_clock_version: 6 });
    expect(queueMove).not.toHaveBeenCalled();
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
  });

  it('reconciles an accepted move after engine failure and resumes only the durable bot version', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const, white_remaining_ms: 181000 } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const opponent = Promise.withResolvers<PlayMoveResponse>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValueOnce(pending).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame)
      .mockResolvedValueOnce({ ...timedGame, pgn: '1. e4 *' }).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Engine failed after accepting move', 503));
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockReturnValue(opponent.promise);
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledWith('timed-session', 5));
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(queueMove).not.toHaveBeenCalled();
    await act(async () => { opponent.resolve({ opponent_move: 'e7e5', status: after.status, clock: after.clock }); });
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active'));
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(1);
    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
    }));
  });

  it('refreshes a stale rejected move without replaying its version or its optimistic board', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const after = { ...timedSession, pgn: '1. d4 d5 *', clock: { ...timedClock, version: 8 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockResolvedValue({ ...timedGame, pgn: '1. d4 d5 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Stale clock version', 409));
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(await screen.findByRole('alert')).toHaveTextContent('Stale clock version');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: 'rnbqkbnr/ppp1pppp/8/3p4/3P4/8/PPP1PPPP/RNBQKBNR w KQkq - 0 2',
    }));
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('never sends or queues a timed move while offline', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(timedSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue(timedGame);
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(ApiClient.getPlaySession).toHaveBeenCalled());
    expect(screen.getByText(/Timed moves require a connection/)).toBeInTheDocument();
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
  });

  it('resumes a remounted bot turn once per version and offers an explicit failed-command retry', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', timedSession.id);
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(pending);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 *' });
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockRejectedValueOnce(new Error('Engine unavailable'))
      .mockResolvedValue({ opponent_move: 'e7e5', status: after.status, clock: after.clock });
    render(<PlayScreen />);
    const retry = await screen.findByRole('button', { name: 'Retry opponent move' });
    expect(screen.getByRole('alert')).toHaveTextContent('Engine unavailable');
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenLastCalledWith('timed-session', 5);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 *' });
    fireEvent.click(retry);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(2);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(getQueuedMoves).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Retry opponent move' })).not.toBeInTheDocument();
  });

  it('waits once for a remounted duplicate opponent command and installs its eventual canonical reply', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', timedSession.id);
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const changed = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(pending).mockResolvedValueOnce(pending)
      .mockReturnValueOnce(changed.promise);
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockRejectedValue(new ApiRequestError('Already in progress', 409));
    render(<PlayScreen />);
    await waitFor(() => expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith(timedSession.id, 5));
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(screen.getByText('Opponent thinking…')).toBeInTheDocument();
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    expect(screen.queryByRole('button', { name: 'Retry opponent move' })).not.toBeInTheDocument();
    await act(async () => { changed.resolve(after); });
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledExactlyOnceWith(timedSession.id, 5);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('retains opponent retry when a manual refresh still has the pending clock version', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', timedSession.id);
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const boundedWait = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(pending).mockResolvedValueOnce(pending)
      .mockReturnValueOnce(boundedWait.promise).mockResolvedValueOnce(pending)
      .mockResolvedValueOnce(pending).mockResolvedValue(after);
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockRejectedValue(new ApiRequestError('Already in progress', 409));
    render(<PlayScreen />);
    await waitFor(() => expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith(timedSession.id, 5));
    await act(async () => { boundedWait.resolve(pending); });
    expect(screen.getByRole('alert')).toHaveTextContent('still pending');
    expect(screen.getByRole('button', { name: 'Retry opponent move' })).toBeEnabled();
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Retry game refresh' })).not.toBeInTheDocument());
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    expect(screen.getByRole('button', { name: 'Retry opponent move' })).toBeEnabled();
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith(timedSession.id);
    fireEvent.click(screen.getByRole('button', { name: 'Retry opponent move' }));
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active'));
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith(timedSession.id, 5);
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(2);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
  });

  it.each(['initial refresh', 'version wait'] as const)('retains opponent retry after a failed %s', async (stage) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', timedSession.id);
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const reads = vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(pending);
    if (stage === 'version wait') reads.mockResolvedValueOnce(pending);
    reads.mockRejectedValueOnce(new Error('Saved position temporarily unavailable'))
      .mockResolvedValueOnce(pending).mockResolvedValueOnce(after);
    vi.mocked(ApiClient.resumePlaySessionOpponentMove)
      .mockRejectedValueOnce(new ApiRequestError('Already in progress', 409))
      .mockResolvedValueOnce({
        opponent_move: 'e7e5', status: 'active', critical_interruption: null,
        clock: { ...timedClock, version: 6 },
      });
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry game refresh' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Retry opponent move' })).toBeEnabled());
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    fireEvent.click(screen.getByRole('button', { name: 'Retry opponent move' }));
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active'));
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(2);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('animates only the active side and refreshes once at zero without inventing a result', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.useFakeTimers();
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const black = { ...timedSession, pgn: '1. e4 *', player_color: 'black' as const, clock: {
      ...timedClock, active_color: 'black' as const, black_remaining_ms: 2500,
    } };
    const deadlineRefresh = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(black).mockReturnValueOnce(deadlineRefresh.promise);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 *' });
    const view = render(<PlayScreen initialSession={black} />);
    await act(async () => {});
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    expect(screen.getByTestId('black-clock')).toHaveTextContent('0:03');
    expect(screen.getByTestId('white-clock')).toHaveTextContent('3:00');
    now = 1100;
    await act(async () => { await vi.advanceTimersByTimeAsync(1100); });
    expect(screen.getByTestId('black-clock')).toHaveTextContent('0:02');
    expect(screen.getByTestId('white-clock')).toHaveTextContent('3:00');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    now = 2600;
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('black-clock')).toHaveTextContent('0:00');
    expect(screen.getByTestId('session-info')).toHaveTextContent('active');
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    now = 62600;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    await act(async () => {
      deadlineRefresh.resolve({
        ...black, status: 'completed', result: '1-0', clock: {
          ...black.clock, version: 5, black_remaining_ms: 0, active_color: null, deadline_at: null,
          terminal_reason: 'flag', flagged_color: 'black',
        },
      });
    });
    expect(screen.getByTestId('game-result')).toHaveTextContent('White wins. Time expired.');
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    view.unmount();
  });

  it.each([false, true])('waits once for a live zero-millisecond clock version and preserves manual recovery (fails=%s)', async fails => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.useFakeTimers();
    vi.spyOn(performance, 'now').mockReturnValue(0);
    const live = { ...timedSession, clock: { ...timedClock, white_remaining_ms: 0 } };
    const ended: PlaySessionRead = { ...live, status: 'completed', result: '0-1', clock: {
      ...live.clock, version: 5, active_color: null, deadline_at: null,
      terminal_reason: 'flag', flagged_color: 'white',
    } };
    const expiry = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockImplementation(async (_sessionId, afterVersion) =>
      afterVersion === undefined ? live : expiry.promise);
    render(<PlayScreen initialSession={live} />);
    await act(async () => {});
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(1, timedSession.id);
    expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(2, timedSession.id, 4);
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId('white-clock')).toHaveTextContent('0:00');
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    if (fails) {
      await act(async () => { expiry.reject(new ApiRequestError('Expiry still pending', 503)); });
      expect(screen.getByRole('alert')).toHaveTextContent('Expiry still pending');
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
      vi.mocked(ApiClient.getPlaySession).mockResolvedValue(ended);
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' })); });
      expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(3, timedSession.id);
    } else {
      await act(async () => { expiry.resolve(ended); });
    }
    expect(screen.getByTestId('game-result')).toHaveTextContent('Black wins. Time expired.');
    expect(screen.queryByRole('button', { name: 'Retry game refresh' })).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(fails ? 3 : 2);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('re-arms a deadline after an unchanged active snapshot without polling a pending read', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.useFakeTimers();
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const live = { ...timedSession, clock: {
      ...timedClock, white_remaining_ms: 1000, deadline_at: '2026-11-19T12:00:01Z',
    } };
    const extended = { ...live, clock: {
      ...live.clock, white_remaining_ms: 2000, server_now: '2026-11-19T11:59:59Z',
    } };
    const firstDeadline = Promise.withResolvers<PlaySessionRead>();
    const secondDeadline = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(live)
      .mockReturnValueOnce(firstDeadline.promise).mockReturnValueOnce(secondDeadline.promise);
    render(<PlayScreen initialSession={live} />);
    await act(async () => {});
    now = 1000;
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(2, live.id, live.clock.version);
    now = 6000;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    await act(async () => { firstDeadline.resolve(extended); });
    expect(screen.getByTestId('white-clock')).toHaveTextContent('0:02');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    now = 8000;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(3, live.id, live.clock.version);
    now = 68000;
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    await act(async () => {
      secondDeadline.resolve({ ...extended, status: 'completed', result: '0-1', clock: {
        ...extended.clock, version: 5, white_remaining_ms: 0, active_color: null, deadline_at: null,
        terminal_reason: 'flag', flagged_color: 'white',
      } });
    });
    expect(screen.getByTestId('game-result')).toHaveTextContent('Black wins. Time expired.');
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('keeps a premove pending until the canonical reply and then uses its new clock version', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const response = Promise.withResolvers<{ opponent_move: string; status: string; clock: PlayClockRead }>();
    const canonical = Promise.withResolvers<PlaySessionRead>();
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const next = { ...timedSession, pgn: '1. e4 e5 2. Nf3 Nc6 *', clock: { ...timedClock, version: 8 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockReturnValueOnce(canonical.promise).mockResolvedValue(next);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(response.promise)
      .mockResolvedValue({ opponent_move: 'b8c6', status: 'active', clock: next.clock });
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    let firstMove: void | Promise<void>;
    await act(async () => { firstMove = getRegisteredMoveHandler()('e2', 'e4'); });
    act(() => {
      chessgroundMock.state.premovable.current = ['g1', 'f3'];
      chessgroundMock.premoveSet?.('g1', 'f3');
    });
    expect(screen.getByRole('button', { name: 'Cancel premove' })).toBeInTheDocument();
    await act(async () => { response.resolve({ opponent_move: 'e7e5', status: 'active', clock: after.clock }); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    await act(async () => { canonical.resolve(after); await firstMove; });
    await waitFor(() => expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(2));
    expect(ApiClient.makePlaySessionMove).toHaveBeenLastCalledWith('timed-session', { move: 'g1f3', expected_clock_version: 6 });
  });

  it('cancels a timed premove when the deferred canonical snapshot replaces its source history', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const response = Promise.withResolvers<PlayMoveResponse>();
    const canonical = Promise.withResolvers<PlaySessionRead>();
    const replacement = { ...timedSession, pgn: '1. d4 d5 *', clock: { ...timedClock, version: 8 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockReturnValueOnce(canonical.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValue(response.promise);
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { getRegisteredMoveHandler()('e2', 'e4'); });
    act(() => {
      chessgroundMock.state.premovable.current = ['g1', 'f3'];
      chessgroundMock.premoveSet?.('g1', 'f3');
    });
    expect(screen.getByRole('button', { name: 'Cancel premove' })).toBeInTheDocument();
    await act(async () => { response.reject(new ApiRequestError('Stale clock version', 409)); });
    expect(screen.getByRole('button', { name: 'Cancel premove' })).toBeInTheDocument();
    await act(async () => { canonical.resolve(replacement); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('d5');
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('never queues an ambiguously accepted timed move when the connection drops during the request', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(pending);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockImplementation(async () => {
      network.mockReturnValue(false);
      window.dispatchEvent(new Event('offline'));
      throw new Error('Connection lost after submission');
    });
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(queueMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
  });

  it('locks a timed failure until canonical refresh succeeds and never retries the stale move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession)
      .mockRejectedValueOnce(new Error('Refresh unavailable')).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new Error('Submission outcome unknown'));
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    await act(async () => { await getRegisteredMoveHandler()('d2', 'd4'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId('retry-game-refresh'));
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('renders the server flag result, including a draw, rather than deriving a client winner', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const ended = { ...timedSession, pgn: '1. e4 1/2-1/2', result: '1/2-1/2', status: 'completed', clock: {
      ...timedClock, version: 5, white_remaining_ms: 0, active_color: null, deadline_at: null,
      terminal_reason: 'flag' as const, flagged_color: 'white' as const,
    } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(ended);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, result: '1/2-1/2', pgn: '1. e4 1/2-1/2' });
    render(<PlayScreen initialSession={timedSession} />);
    expect(await screen.findByTestId('game-result')).toHaveTextContent('Draw. Time expired.');
    expect(screen.getByTestId('game-result')).not.toHaveTextContent('wins');
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock');
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
  });

  it('preserves candidate generation and clock-version fences together on timed protected moves', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const after = { ...timedSession, pgn: '1. e4 e5 *', assistance_modes: ['candidates' as const],
      clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(after);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({ ...blunderVerdict, mode: 'candidates',
      assistance_modes: ['candidates'], grades: [{ ...blunderVerdict.grades[0], band: 'sound' }] });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active', clock: after.clock });
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Play e4' }));
    await waitFor(() => expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('timed-session', {
      move: 'e2e4', expected_generation: 7, expected_clock_version: 4,
    }));
    await waitFor(() => expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('Candidates'));
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('refreshes a successful protected timed commit after a deadline refresh has already changed the source position', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.useFakeTimers();
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const initial = { ...timedSession, clock: { ...timedClock, white_remaining_ms: 1 } };
    const pending = { ...timedSession, pgn: '1. e4 *', clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    const committed = Promise.withResolvers<PlayMoveResponse>();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(initial).mockResolvedValueOnce(pending).mockResolvedValue(after);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [{ ...blunderVerdict.grades[0], band: 'sound' }],
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValue(committed.promise);
    render(<PlayScreen initialSession={initial} />);
    await act(async () => {});
    fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' })); });
    fireEvent.click(screen.getByRole('button', { name: 'Play e4' }));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith(timedSession.id, {
      move: 'e2e4', expected_generation: 7, expected_clock_version: 4,
    });
    now = 2;
    await act(async () => { await vi.advanceTimersByTimeAsync(2); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4');
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
    await act(async () => { committed.resolve({ opponent_move: 'e7e5', status: 'active', clock: after.clock }); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(screen.queryByTestId('retry-game-refresh')).not.toBeInTheDocument();
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it.each([
    { protectedMove: false, canonicalVersion: 6 },
    { protectedMove: true, canonicalVersion: 6 },
    { protectedMove: false, canonicalVersion: 8 },
    { protectedMove: true, canonicalVersion: 8 },
  ])('retains a version-bound timed coach lesson across failed GET and retry: %j', async ({ protectedMove, canonicalVersion }) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const coach = { ...timedSession, coach_mode: true };
    const committed = Promise.withResolvers<PlayMoveResponse>();
    const refresh = Promise.withResolvers<PlaySessionRead>();
    const retry = Promise.withResolvers<PlaySessionRead>();
    const after = { ...coach, pgn: canonicalVersion === 6 ? '1. e4 e5 *' : '1. e4 e5 2. Nf3 Nc6 *',
      clock: { ...timedClock, version: canonicalVersion } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(coach).mockReturnValueOnce(refresh.promise)
      .mockReturnValueOnce(retry.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValue(committed.promise);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [{ ...blunderVerdict.grades[0], band: 'sound' }],
    });
    render(<PlayScreen initialSession={coach} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    if (protectedMove) {
      fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
      fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Play e4' }));
    } else await act(async () => { getRegisteredMoveHandler()('e2', 'e4'); });
    await act(async () => {
      committed.resolve({
        opponent_move: 'e7e5', status: 'active', clock: { ...timedClock, version: 6 },
        critical_interruption: { lesson: serverLesson, opportunity_id: 'timed-opportunity', study_session_id: 'timed-study' },
      });
    });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    await act(async () => { refresh.reject(new Error('Canonical refresh unavailable')); });
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    await act(async () => { retry.resolve(after); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent(canonicalVersion === 6 ? 'e5' : 'Nc6');
    if (canonicalVersion === 6) {
      expect(screen.getByTestId('critical-moment-review')).toHaveAttribute('data-opportunity-id', 'timed-opportunity');
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    } else {
      expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    }
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith(timedSession.id, {
      move: 'e2e4', expected_clock_version: 4, ...(protectedMove ? { expected_generation: 7 } : {}),
    });
    expect(queueMove).not.toHaveBeenCalled();
  });

  it.each([false, true])('does not expose a timed coach lesson across an owner switch during canonical GET (protected=%s)', async protectedMove => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const coach = { ...timedSession, coach_mode: true };
    const committed = Promise.withResolvers<PlayMoveResponse>();
    const canonical = Promise.withResolvers<PlaySessionRead>();
    const after = { ...coach, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(coach).mockReturnValueOnce(canonical.promise).mockResolvedValue(after);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValue(committed.promise);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [{ ...blunderVerdict.grades[0], band: 'sound' }],
    });
    render(<PlayScreen initialSession={coach} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    if (protectedMove) {
      fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
      fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Play e4' }));
    } else await act(async () => { getRegisteredMoveHandler()('e2', 'e4'); });
    await act(async () => {
      committed.resolve({
        opponent_move: 'e7e5', status: 'active', clock: after.clock,
        critical_interruption: { lesson: serverLesson, opportunity_id: 'owner-fenced', study_session_id: 'owner-study' },
      });
    });
    localStorage.setItem('scan64_player_id', 'other-player');
    await act(async () => { canonical.resolve(after); });
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Return to the game owner');
    localStorage.setItem('scan64_player_id', 'test-player');
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(await screen.findByTestId('critical-moment-review')).toHaveAttribute('data-opportunity-id', 'owner-fenced');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(queueMove).not.toHaveBeenCalled();
  });

  it.each([
    { protectedMove: false, canonicalVersion: 6 },
    { protectedMove: true, canonicalVersion: 6 },
    { protectedMove: false, canonicalVersion: 8 },
    { protectedMove: true, canonicalVersion: 8 },
  ])('retains a committed timed coach lesson across an identity switch during POST without disclosing it: %j', async ({ protectedMove, canonicalVersion }) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const coach = { ...timedSession, coach_mode: true };
    const committed = Promise.withResolvers<PlayMoveResponse>();
    const canonical = Promise.withResolvers<PlaySessionRead>();
    const after = { ...coach, pgn: canonicalVersion === 6 ? '1. e4 e5 *' : '1. e4 e5 2. Nf3 Nc6 *',
      clock: { ...timedClock, version: canonicalVersion } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(coach).mockReturnValueOnce(canonical.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValue(committed.promise);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [{ ...blunderVerdict.grades[0], band: 'sound' }],
    });
    render(<PlayScreen initialSession={coach} />);
    await act(async () => {});
    if (protectedMove) {
      fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
      fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
      fireEvent.click(await screen.findByRole('button', { name: 'Play e4' }));
    } else await act(async () => { getRegisteredMoveHandler()('e2', 'e4'); });
    localStorage.setItem('scan64_player_id', 'other-player');
    await act(async () => {
      committed.resolve({
        opponent_move: 'e7e5', status: 'active', clock: { ...timedClock, version: 6 },
        critical_interruption: { lesson: serverLesson, opportunity_id: 'post-owner-fenced', study_session_id: 'post-owner-study' },
      });
    });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Return to the game owner');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    localStorage.setItem('scan64_player_id', 'test-player');
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    await act(async () => { canonical.resolve(after); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent(canonicalVersion === 6 ? 'e5' : 'Nc6');
    if (canonicalVersion === 6) {
      expect(screen.getByTestId('critical-moment-review')).toHaveAttribute('data-opportunity-id', 'post-owner-fenced');
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    } else {
      expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
      expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    }
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith(timedSession.id, {
      move: 'e2e4', expected_clock_version: 4, ...(protectedMove ? { expected_generation: 7 } : {}),
    });
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it.each(['id', 'player_id'] as const)('rejects a timed coach refresh with a mismatched %s before showing its lesson', async field => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const coach = { ...timedSession, coach_mode: true };
    const canonical = Promise.withResolvers<PlaySessionRead>();
    const after = { ...coach, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(coach).mockReturnValueOnce(canonical.promise).mockResolvedValue(after);
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: 'e7e5', status: 'active', clock: after.clock,
      critical_interruption: { lesson: serverLesson, opportunity_id: 'matched-snapshot', study_session_id: 'matched-study' },
    });
    render(<PlayScreen initialSession={coach} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { getRegisteredMoveHandler()('e2', 'e4'); });
    await act(async () => { canonical.resolve({ ...after, [field]: 'different-owner-or-session' }); });
    expect(screen.queryByTestId('critical-moment-review')).not.toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('does not match its owner and session');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(await screen.findByTestId('critical-moment-review')).toHaveAttribute('data-opportunity-id', 'matched-snapshot');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('reconciles a flag during guard confirmation without committing or queuing a stale checked move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const ended = { ...timedSession, status: 'completed', result: '0-1', assistance_modes: ['guard' as const],
      clock: { ...timedClock, version: 5, active_color: null, deadline_at: null,
        terminal_reason: 'flag' as const, flagged_color: 'white' as const, white_remaining_ms: 0 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(ended);
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Clock expired', 409));
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    fireEvent.click(await screen.findByRole('button', { name: 'Play anyway' }));
    expect(await screen.findByTestId('game-result')).toHaveTextContent('Black wins. Time expired.');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('timed-session', {
      move: 'e2e4', expected_generation: 7, expected_clock_version: 4,
    });
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
    expect(queueMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
  });

  it('refreshes authoritative conversion outcome after a move and retries the same source with the chosen bot and a fresh session', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const sourceFen = '7k/8/8/8/8/r7/6K1/8 w - - 0 1';
    const attempt = {
      id: 'attempt-1', play_session_id: 'convert-1', source_game_id: 'original-game',
      source_review_id: 'review-1', source_position_id: 'position-1', source_analysis_id: 'analysis-1',
      source_ply: 12, source_fen: sourceFen, player_color: 'black' as const, seed: 'server-seed-1',
      completed_at: null, result: null, outcome: null, verification_status: 'verified' as const, next_review_at: null,
    };
    const active: PlaySessionRead = { id: 'convert-1', player_id: 'test-player', game_id: 'custom-1',
      opponent_config: { provider: 'stockfish', bot_level: '1', initial_ply: '1' }, player_color: 'black',
      status: 'active', coach_mode: false, assistance_modes: [], opponent_move: 'g2f2', sparring: attempt };
    const ended: PlaySessionRead = { ...active, status: 'completed', sparring: { ...attempt,
      result: '1/2-1/2', outcome: 'draw', completed_at: '2026-11-19T10:00:00Z', next_review_at: '2026-11-20T10:00:00Z' } };
    const fresh: PlaySessionRead = { ...active, id: 'convert-2', game_id: 'custom-2',
      opponent_config: { provider: 'stockfish', bot_level: '8', initial_ply: '1' }, opponent_move: 'g2h2',
      sparring: { ...attempt, id: 'attempt-2', play_session_id: 'convert-2', seed: 'server-seed-2' } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(active);
    const terminal = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockReturnValueOnce(terminal.promise);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'custom-1',
      pgn: `[SetUp "1"]\n[FEN "${sourceFen}"]\n\n1. Kf2 *`, white: 'bot', black: 'human', result: '*' });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: null, status: 'completed' });
    vi.mocked(ApiClient.createPlaySession).mockRejectedValueOnce(new ApiRequestError('Conversion start unavailable', 503)).mockResolvedValue(fresh);
    render(<PlayScreen initialSession={active} />);
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('conversion practice');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      fen: '7k/8/8/8/8/r7/5K2/8 b - - 1 1', movable: expect.objectContaining({ color: 'black' }) }));
    await act(async () => { await getRegisteredMoveHandler()('a3', 'a1'); });
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith('convert-1');
    expect(screen.getByText('Loading recorded conversion result…')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Retry conversion practice' })).toBeDisabled();
    await act(async () => { terminal.resolve(ended); });
    const result = screen.getByRole('region', { name: 'Conversion result' });
    expect(result).toHaveTextContent('Verified conversion practice · draw · 1/2-1/2');
    expect(result).toHaveTextContent('Next review: 2026-11-20T10:00:00Z');
    expect(result).not.toHaveTextContent('Game ended.');
    fireEvent.change(screen.getByRole('combobox', { name: 'Conversion bot level' }), { target: { value: '8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Retry conversion practice' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Conversion start unavailable');
    expect(screen.getByTestId('session-info')).toHaveTextContent('Status: completed');
    expect(screen.queryByTestId('start-btn')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry conversion practice' }));
    await waitFor(() => expect(screen.getByTestId('session-info')).toHaveTextContent('Status: active'));
    expect(ApiClient.createPlaySession).toHaveBeenCalledWith({
      player_id: 'test-player', opponent_config: { provider: 'stockfish', bot_level: '8' },
      sparring_source: { game_id: 'original-game', ply: 12 },
    });
    expect(ApiClient.createPlayer).not.toHaveBeenCalled();
    expect(ApiClient.createGame).not.toHaveBeenCalled();
    expect(screen.queryByTestId('start-btn')).not.toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Conversion result' })).not.toBeInTheDocument();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      fen: '7k/8/8/8/8/r7/7K/8 b - - 1 1', movable: expect.objectContaining({ color: 'black' }) }));
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe('convert-2');
    expect(screen.getByText(/Practice session convert-2 · seed server-seed-2/)).toBeVisible();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...fresh, status: 'completed', sparring: {
      ...fresh.sparring!, result: '0-1', outcome: 'win', completed_at: '2026-11-19T11:00:00Z', next_review_at: '2026-11-22T11:00:00Z',
    } });
    await act(async () => { await getRegisteredMoveHandler()('a3', 'a1'); });
    await waitFor(() => expect(screen.getByRole('region', { name: 'Conversion result' })).toHaveTextContent('win · 0-1'));
    expect(screen.queryByRole('button', { name: 'Retry conversion practice' })).not.toBeInTheDocument();
  });

  it('refreshes a resigned conversion and keeps imported outcomes historical', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const active: PlaySessionRead = { id: 'conversion-resign', player_id: 'test-player', game_id: 'custom-game',
      opponent_config: { bot_level: '1' }, player_color: 'white', status: 'active', coach_mode: false, assistance_modes: [],
      sparring: { id: 'attempt-1', play_session_id: 'conversion-resign', source_game_id: 'original-game',
        source_review_id: 'review-1', source_position_id: 'position-1', source_analysis_id: 'analysis-1',
        source_ply: 2, source_fen: '7k/8/5KQ1/8/8/8/8/8 w - - 0 1', player_color: 'white', seed: 'seed',
        completed_at: null, result: null, outcome: null, verification_status: 'verified', next_review_at: null } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(active);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'custom-game',
      pgn: `[SetUp "1"]\n[FEN "${active.sparring!.source_fen}"]\n\n*`, white: 'human', black: 'bot', result: '*' });
    vi.mocked(ApiClient.resignPlaySession).mockResolvedValue({ ...active, status: 'completed' });
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce({ ...active, status: 'completed', sparring: {
      ...active.sparring!, outcome: 'loss', result: '0-1', completed_at: '2026-11-19T10:00:00Z',
      verification_status: 'historical', next_review_at: '2026-11-20T10:00:00Z',
    } });
    render(<PlayScreen initialSession={active} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    const result = await screen.findByRole('region', { name: 'Conversion result' });
    await waitFor(() => expect(result).toHaveTextContent('Historical result (not verified) · loss · 0-1'));
    expect(result).toHaveTextContent('Next review: 2026-11-20T10:00:00Z');
    expect(within(result).queryByText('Verified conversion practice')).not.toBeInTheDocument();
    expect(result).not.toHaveTextContent('You resigned. Opponent wins.');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
  });

  it('reconstructs an active conversion after restart from its custom game, not the original source', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'conversion-resume');
    const sourceFen = '7k/8/8/8/8/r7/6K1/8 b - - 0 1';
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'conversion-resume', player_id: 'test-player', game_id: 'custom-resume',
      opponent_config: { bot_level: '8' }, player_color: 'black', status: 'active', coach_mode: false, assistance_modes: [],
      sparring: { id: 'attempt-1', play_session_id: 'conversion-resume', source_game_id: 'original-game',
        source_review_id: 'review-1', source_position_id: 'position-1', source_analysis_id: 'analysis-1',
        source_ply: 12, source_fen: sourceFen, player_color: 'black', seed: 'seed',
        completed_at: null, result: null, outcome: null, verification_status: 'verified', next_review_at: null },
    });
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'custom-resume',
      pgn: `[SetUp "1"]\n[FEN "${sourceFen}"]\n\n1... Ra1 2. Kf2 *`, white: 'bot', black: 'human', result: '*' });
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(ApiClient.getGame).toHaveBeenCalledTimes(1);
    expect(ApiClient.getGame).toHaveBeenCalledWith('custom-resume');
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('conversion practice');
    await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      fen: '7k/8/8/8/8/8/5K2/r7 b - - 2 2', movable: expect.objectContaining({ color: 'black' }) })));
  });

  it.each(['initial', 'query', 'storage'] as const)(
    'restores a completed conversion through %s from its final custom PGN without replaying queued moves',
    async entry => {
      const sourceFen = '7k/8/5KQ1/8/8/8/8/8 w - - 0 1';
      const completed: PlaySessionRead = {
        id: 'conversion-terminal', player_id: 'test-player', game_id: 'custom-terminal',
        opponent_config: { bot_level: '8', player_color: 'white' }, player_color: 'white',
        status: 'completed', coach_mode: false, assistance_modes: [],
        sparring: { id: 'attempt-terminal', play_session_id: 'conversion-terminal', source_game_id: 'original-game',
          source_review_id: 'review-1', source_position_id: 'position-1', source_analysis_id: 'analysis-1',
          source_ply: 12, source_fen: sourceFen, player_color: 'white', seed: 'terminal-seed',
          completed_at: '2026-11-19T10:00:00Z', result: '1-0', outcome: 'win',
          verification_status: 'verified', next_review_at: '2026-11-22T10:00:00Z' },
      };
      if (entry === 'query') {
        window.history.replaceState(null, '', '/play?session=conversion-terminal&mode=study#board');
        localStorage.setItem('scan64_active_play_session_id', 'another-active');
      } else if (entry === 'storage') {
        localStorage.setItem('scan64_active_play_session_id', completed.id);
      }
      vi.mocked(ApiClient.getPlaySession).mockResolvedValue(completed);
      vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'custom-terminal',
        pgn: `[SetUp "1"]\n[FEN "${sourceFen}"]\n[Result "1-0"]\n\n1. Qg7# 1-0`,
        white: 'human', black: 'bot', result: '1-0' });
      vi.mocked(getQueuedMoves).mockResolvedValue([
        { sessionId: completed.id, move: 'g6g7', timestamp: 1 },
      ]);

      render(entry === 'initial' ? <PlayScreen initialSession={{
        ...completed, sparring: { ...completed.sparring!, outcome: null, result: null },
      }} /> : <PlayScreen />);

      await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({
        orientation: 'white', fen: '7k/6Q1/5K2/8/8/8/8/8 b - - 1 1',
        movable: expect.objectContaining({ color: undefined }),
      })));
      const result = screen.getByRole('region', { name: 'Conversion result' });
      await waitFor(() => expect(result).toHaveTextContent('Verified conversion practice · win · 1-0'));
      expect(result).toHaveTextContent('Next review: 2026-11-22T10:00:00Z');
      expect(screen.getByTestId('session-info')).toHaveTextContent('Status: completed');
      expect(screen.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', '/games/custom-terminal/analysis');
      expect(screen.queryByTestId('start-btn')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Resign' })).not.toBeInTheDocument();
      expect(ApiClient.getPlaySession).toHaveBeenCalledWith(completed.id);
      expect(ApiClient.getPlaySession).not.toHaveBeenCalledWith('another-active');
      expect(ApiClient.getGame).toHaveBeenCalledExactlyOnceWith('custom-terminal');
      expect(getQueuedMoves).not.toHaveBeenCalled();
      expect(syncQueuedMoves).not.toHaveBeenCalled();
      expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
      expect(localStorage.getItem('scan64_active_play_session_id')).toBe(entry === 'query' ? 'another-active' : null);
      if (entry === 'query') {
        expect(window.location.search).toBe('?mode=study');
        expect(window.location.hash).toBe('#board');
      }
    },
  );

  it('keeps an initially linked conversion locked until its queued move is drained and reloaded', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const sourceFen = '7k/8/8/8/8/r7/6K1/8 b - - 0 1';
    const active: PlaySessionRead = {
      id: 'conversion-queued', player_id: 'test-player', game_id: 'custom-queued',
      opponent_config: { bot_level: '1' }, player_color: 'black', status: 'active', coach_mode: false, assistance_modes: [],
      sparring: { id: 'attempt-queued', play_session_id: 'conversion-queued', source_game_id: 'original-game',
        source_review_id: 'review-1', source_position_id: 'position-1', source_analysis_id: 'analysis-1',
        source_ply: 12, source_fen: sourceFen, player_color: 'black', seed: 'seed',
        completed_at: null, result: null, outcome: null, verification_status: 'verified', next_review_at: null },
    };
    vi.mocked(getQueuedMoves).mockResolvedValue([
      { sessionId: active.id, move: 'a3a1', timestamp: 1 },
    ]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(active);
    const reloaded = Promise.withResolvers<GameRead>();
    vi.mocked(ApiClient.getGame).mockReturnValue(reloaded.promise);
    render(<PlayScreen initialSession={active} />);
    await screen.findByTestId('retry-move-sync');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    const move = await waitFor(getRegisteredMoveHandler);
    await act(async () => { await move('a3', 'a2'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();

    vi.mocked(getQueuedMoves).mockResolvedValue([]);
    fireEvent.click(screen.getByTestId('retry-move-sync'));
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalledExactlyOnceWith(active.game_id));
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();

    await act(async () => reloaded.resolve({
      id: 'custom-queued', pgn: `[SetUp "1"]\n[FEN "${sourceFen}"]\n\n1... Ra1 2. Kf2 *`,
      white: 'bot', black: 'human', result: '*',
    }));
    await waitFor(() => {
      expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({
        orientation: 'black', fen: '7k/8/8/8/8/8/5K2/r7 b - - 2 2',
        movable: expect.objectContaining({ color: 'black' }),
      }));
      expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
    });
  });

  it('grades three unique legal candidates without moving, then commits only the chosen generation-bound move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const grading = Promise.withResolvers<PlayAssistanceRead>();
    const commit = Promise.withResolvers<{ opponent_move: string; status: string }>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(grading.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(commit.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    const choose = screen.getByRole('combobox', { name: 'Add candidate move' });
    for (const move of ['e2e4', 'd2d4', 'g1f3']) fireEvent.change(choose, { target: { value: move } });
    expect(choose).toBeDisabled();
    expect(screen.queryByRole('option', { name: 'e4' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
    expect(screen.getByText('Grading candidates… No move has been played.')).toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: startingFen }));
    await act(async () => grading.resolve({
      fen: startingFen, generation: 11, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [
        { move: 'e2e4', san: 'e4', band: 'sound', win_probability_loss: 0 },
        { move: 'd2d4', san: 'd4', band: 'inaccuracy', win_probability_loss: 0.03 },
        { move: 'g1f3', san: 'Nf3', band: 'mistake', win_probability_loss: 0.1 },
      ],
    }));
    expect(ApiClient.gradePlayMoves).toHaveBeenCalledExactlyOnceWith('assisted-game', {
      player_id: 'test-player', fen: startingFen, mode: 'candidates', moves: ['e2e4', 'd2d4', 'g1f3'],
    });
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('Candidates');
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    fireEvent.click(screen.getByRole('button', { name: 'Play d4' }));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('assisted-game', {
      move: 'd2d4', expected_generation: 11,
    });
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: startingFen }));
    const position = screen.getByTestId('chessground-board')
      .closest('.coach-board')?.querySelector(':scope > p[role="status"][aria-atomic="true"]');
    if (!position) throw new Error('Board move announcement is missing');
    const announcements: string[] = [];
    const observer = new MutationObserver(records => {
      announcements.push(position.textContent ?? '');
      for (const record of records) if (record.oldValue) announcements.push(record.oldValue);
    });
    observer.observe(position, {
      subtree: true, childList: true, characterData: true, characterDataOldValue: true,
    });
    try {
      await act(async () => commit.resolve({ opponent_move: 'd7d5', status: 'active' }));
      await waitFor(() => expect(announcements.some(text => /\bd4\b/.test(text))).toBe(true));
    } finally {
      observer.disconnect();
    }
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('d4');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('d5');
    expect(screen.queryByRole('list', { name: 'Candidate grades' })).not.toBeInTheDocument();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('holds a guarded blunder at the original board, preserves assistance on Cancel, and requires Play anyway', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active' });
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    expect(screen.getByRole('alertdialog', { name: 'Blunder warning' })).toHaveTextContent('e4 is a blunder');
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: startingFen }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Play anyway' }));
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('assisted-game', {
      move: 'e2e4', expected_generation: 7,
    });
  });

  it('commits a non-blunder guard verdict with its exact generation, without an optimistic board change', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const commit = Promise.withResolvers<{ opponent_move: string; status: string }>();
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, generation: 9,
      grades: [{ move: 'e2e4', san: 'e4', band: 'mistake', win_probability_loss: 0.12 }],
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(commit.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('assisted-game', { move: 'e2e4', expected_generation: 9 });
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: startingFen }));
    await act(async () => commit.resolve({ opponent_move: 'e7e5', status: 'active' }));
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
  });

  it.each([404, 409, 503])('never bypasses a rejected guard check (%s)', async status => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.gradePlayMoves).mockRejectedValue(new ApiRequestError('Check rejected', status));
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    expect(screen.getByRole('alert')).toHaveTextContent('No move was played');
    expect(screen.getByRole('checkbox', { name: 'Blunder guard' })).toBeChecked();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
    if (status === 409) expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
  });

  it('ignores a cancelled late guard verdict while a newer move is awaiting confirmation', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const older = Promise.withResolvers<PlayAssistanceRead>();
    const newer = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(older.promise).mockReturnValueOnce(newer.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel checking' }));
    await act(async () => getRegisteredMoveHandler()('d2', 'd4'));
    await act(async () => newer.resolve({
      ...blunderVerdict, generation: 8,
      grades: [{ move: 'd2d4', san: 'd4', band: 'blunder', win_probability_loss: 0.4 }],
    }));
    await act(async () => older.resolve({
      ...blunderVerdict, grades: [{ move: 'e2e4', san: 'e4', band: 'sound', win_probability_loss: 0 }],
    }));
    expect(screen.getByRole('alertdialog')).toHaveTextContent('d4 is a blunder');
    expect(screen.getByRole('alertdialog')).not.toHaveTextContent('e4');
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
  });

  it('requires canonical refresh after a protected completion is discarded across an actor change', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const commit = Promise.withResolvers<{ opponent_move: string; status: string }>();
    const refreshed = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(commit.promise);
    vi.mocked(ApiClient.getPlaySession).mockReturnValueOnce(refreshed.promise);
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '1. e4 e5 *', white: 'human', black: 'bot', result: '*',
    });
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Play anyway' }));
    localStorage.setItem('scan64_player_id', 'other-player');
    await act(async () => commit.resolve({ opponent_move: 'e7e5', status: 'active' }));
    expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    localStorage.setItem('scan64_player_id', 'test-player');
    fireEvent.click(screen.getByTestId('retry-game-refresh'));
    await act(async () => getRegisteredMoveHandler()('d2', 'd4'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    await act(async () => refreshed.resolve({ ...assistanceSession, assistance_modes: ['guard'] }));
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5'));
    expect(screen.queryByTestId('retry-game-refresh')).not.toBeInTheDocument();
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
  });

  it('retains late durable disclosure after Cancel and an ordinary move without exposing stale grades', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const grading = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(grading.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active' });
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'e2e4' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel checking' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    await act(async () => grading.resolve({
      ...blunderVerdict, mode: 'candidates', assistance_modes: ['candidates'],
    }));
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('Candidates');
    expect(screen.queryByRole('list', { name: 'Candidate grades' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Play e4' })).not.toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('assisted-game', { move: 'e2e4' });
  });

  it('refreshes a failed protected move then explicitly resumes only the saved bot turn', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Engine failed', 500));
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...assistanceSession, assistance_modes: ['guard'] });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '1. e4 *', white: 'human', black: 'bot', result: '*',
    });
    const reply = Promise.withResolvers<{ opponent_move: string; status: string }>();
    vi.mocked(ApiClient.resumePlaySession).mockReturnValueOnce(reply.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Play anyway' }));
    fireEvent.click(await screen.findByTestId('retry-game-refresh'));
    const resume = await screen.findByRole('button', { name: 'Resume bot turn' });
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4'));
    expect(ApiClient.resumePlaySession).not.toHaveBeenCalled();
    fireEvent.click(resume);
    await act(async () => getRegisteredMoveHandler()('d2', 'd4'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    await act(async () => reply.resolve({ opponent_move: 'e7e5', status: 'active' }));
    expect(ApiClient.resumePlaySession).toHaveBeenCalledExactlyOnceWith('assisted-game', 'test-player');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    expect(screen.queryByRole('button', { name: 'Resume bot turn' })).not.toBeInTheDocument();
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('rejects a guard response after ownership changes, rather than disclosing or playing it', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const grading = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(grading.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    localStorage.setItem('scan64_player_id', 'other-player');
    await act(async () => grading.resolve(blunderVerdict));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('play-assistance-used')).not.toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('game changed');
  });

  it('never queues a generation-bound confirmation when connectivity is lost during submission', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    const commit = Promise.withResolvers<{ opponent_move: string; status: string }>();
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(commit.promise);
    try {
      render(<PlayScreen initialSession={assistanceSession} />);
      fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
      await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
      fireEvent.click(screen.getByRole('button', { name: 'Play anyway' }));
      await act(async () => {
        network.mockReturnValue(false);
        window.dispatchEvent(new Event('offline'));
        commit.reject(new TypeError('Connection lost'));
      });
      expect(queueMove).not.toHaveBeenCalled();
      expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
      expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
      expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
    } finally { network.mockRestore(); }
  });

  it('restores permanent assistance provenance from the required session DTO on reload', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', assistanceSession.id);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...assistanceSession, assistance_modes: ['candidates', 'guard'] });
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'game-123', pgn: '*', white: 'human', black: 'bot', result: '*' });
    render(<PlayScreen />);
    expect(await screen.findByTestId('play-assistance-used')).toHaveTextContent('Candidates, blunder guard');
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('permanently');
    expect(ApiClient.gradePlayMoves).not.toHaveBeenCalled();
  });
  it('invalidates a rejected protected generation and requires canonical refresh instead of sending an ordinary move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue(blunderVerdict);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new ApiRequestError('Position generation changed', 409));
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Play anyway' }));
    await screen.findByTestId('retry-game-refresh');
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('d2', 'd4'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('assisted-game', { move: 'e2e4', expected_generation: 7 });
    expect(queueMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
  });

  it('does not revive old candidates after takeback and replay return to the identical FEN', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const oldGrades = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(oldGrades.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active' });
    vi.mocked(ApiClient.takebackPlaySession).mockResolvedValue(assistanceSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'game-123', pgn: '*', white: 'human', black: 'bot', result: '*' });
    render(<PlayScreen initialSession={assistanceSession} />);
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    const repeated = new Chess();
    repeated.move('e4'); repeated.move('e5');
    fireEvent.change(screen.getByRole('combobox', { name: 'Add candidate move' }), { target: { value: 'g1f3' } });
    fireEvent.click(screen.getByRole('button', { name: 'Grade candidates' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel checking' }));
    fireEvent.click(screen.getByTestId('takeback-btn'));
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement());
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    await act(async () => oldGrades.resolve({
      fen: repeated.fen(), generation: 1, mode: 'candidates', assistance_modes: ['candidates'],
      grades: [{ move: 'g1f3', san: 'Nf3', band: 'sound', win_probability_loss: 0 }],
    }));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: repeated.fen() }));
    expect(screen.queryByRole('button', { name: 'Play Nf3' })).not.toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(2);
  });

  it('does not commit or display a guard verdict after its screen is unmounted', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const pending = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(pending.promise);
    const view = render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    view.unmount();
    await act(async () => pending.resolve({
      ...blunderVerdict, grades: [{ move: 'e2e4', san: 'e4', band: 'sound', win_probability_loss: 0 }],
    }));
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('does not treat an unscored guard verdict as permission to play', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.gradePlayMoves).mockResolvedValue({
      ...blunderVerdict, grades: [{ move: 'e2e4', san: 'e4', band: 'unscored', win_probability_loss: 0 }],
    });
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('complete scored move grades');
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
  });
  it('keeps a cancelled check undisclosed while showing assistance persisted before its response arrived', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const pending = Promise.withResolvers<PlayAssistanceRead>();
    vi.mocked(ApiClient.gradePlayMoves).mockReturnValueOnce(pending.promise);
    render(<PlayScreen initialSession={assistanceSession} />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Blunder guard' }));
    await act(async () => getRegisteredMoveHandler()('e2', 'e4'));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel checking' }));
    await act(async () => pending.resolve(blunderVerdict));
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument();
    expect(screen.getByTestId('play-assistance-used')).toHaveTextContent('blunder guard');
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
  });



  it('resumes the explicitly linked unfinished session instead of a different stored session', async () => {
    window.history.replaceState(null, '', '/play?session=blocked-session');
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'different-session');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'blocked-session', player_id: 'test-player', game_id: 'blocked-game',
      opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [],
    });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'blocked-game', pgn: '1. e4 e5 *', white: 'test-player', black: 'Stockfish', result: '*',
    });
    try {
      render(<PlayScreen />);
      await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
      expect(ApiClient.getPlaySession).toHaveBeenCalledWith('blocked-session');
      expect(ApiClient.getPlaySession).not.toHaveBeenCalledWith('different-session');
      expect(ApiClient.getGame).toHaveBeenCalledWith('blocked-game');
    } finally {
      window.history.replaceState(null, '', '/');
    }
  });

  it.each(['ended', '404', '422'])('consumes a stale %s query and recovers a different stored game', async reason => {
    window.history.replaceState(null, '', '/play?session=stale&mode=study#board');
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'stored-a');
    const active: PlaySessionRead = { id: 'stored-a', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    if (reason === 'ended') vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce({ ...active, id: 'stale', status: 'completed' });
    else vi.mocked(ApiClient.getPlaySession).mockRejectedValueOnce(new ApiRequestError('Unavailable', Number(reason)));
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(active);
    const view = render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(ApiClient.getPlaySession).toHaveBeenCalledWith('stored-a');
    expect(window.location.search).toBe('?mode=study');
    expect(window.location.hash).toBe('#board');
    view.unmount();
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith('stored-a');
  });

  it.each([false, true])('falls back from an ended timed link only when a different game is stored (same=%s)', async sameStoredGame => {
    window.history.replaceState(null, '', '/play?session=explicit-b&mode=study#board');
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', sameStoredGame ? 'explicit-b' : 'stored-a');
    const ended: PlaySessionRead = { ...timedSession, id: 'explicit-b', pgn: '1. e4 1-0', result: '1-0', status: 'completed',
      clock: { ...timedClock, version: 5, black_remaining_ms: 0, active_color: null, deadline_at: null,
        terminal_reason: 'flag', flagged_color: 'black' } };
    const active = { ...timedSession, id: 'stored-a', pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(ended).mockResolvedValue(active);
    render(<PlayScreen />);
    if (sameStoredGame) {
      expect(await screen.findByTestId('game-result')).toHaveTextContent('White wins. Time expired.');
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
      expect(ApiClient.getPlaySession).toHaveBeenCalledExactlyOnceWith('explicit-b');
      expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4');
    } else {
      await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
      expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(1, 'explicit-b');
      expect(ApiClient.getPlaySession).toHaveBeenNthCalledWith(2, 'stored-a');
      expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
      expect(localStorage.getItem('scan64_active_play_session_id')).toBe('stored-a');
      expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
      expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    }
    expect(window.location.search).toBe('?mode=study');
    expect(window.location.hash).toBe('#board');
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(syncQueuedMoves).not.toHaveBeenCalled();
  });

  it.each([
    { entry: 'storage', failure: '503' },
    { entry: 'storage', failure: 'network' },
    { entry: 'query', failure: '503' },
    { entry: 'query', failure: 'network' },
  ])('retries the initial saved timed read manually after a transient failure: %j', async ({ entry, failure }) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const storedId = entry === 'query' ? 'preserved-a' : timedSession.id;
    localStorage.setItem('scan64_active_play_session_id', storedId);
    if (entry === 'query') window.history.replaceState(null, '', `/play?session=${timedSession.id}&mode=study#board`);
    const cause = failure === '503' ? new ApiRequestError('Expiry temporarily pending', 503) : new Error('Connection lost');
    const retry = Promise.withResolvers<PlaySessionRead>();
    const saved = { ...timedSession, pgn: '1. e4 e5 *', clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockRejectedValueOnce(cause).mockRejectedValueOnce(cause)
      .mockReturnValueOnce(retry.promise);
    render(<PlayScreen />);
    expect(await screen.findByRole('alert')).toHaveTextContent(`Unable to resume game: ${cause.message}`);
    expect(screen.queryByTestId('start-btn')).not.toBeInTheDocument();
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe(storedId);
    expect(ApiClient.getPlaySession).toHaveBeenCalledExactlyOnceWith(timedSession.id);
    if (entry === 'query') expect(window.location.search).toBe(`?session=${timedSession.id}&mode=study`);
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' })); });
    expect(screen.getByRole('alert')).toHaveTextContent(cause.message);
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe(storedId);
    fireEvent.click(screen.getByRole('button', { name: 'Retry game refresh' }));
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
    expect(screen.getByTestId('resuming-game')).toBeInTheDocument();
    await act(async () => { retry.resolve(saved); });
    expect(screen.getByTestId('session-info')).toHaveTextContent('Status: active');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(screen.queryByRole('button', { name: 'Retry game refresh' })).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe(storedId);
    expect(vi.mocked(ApiClient.getPlaySession).mock.calls).toEqual([[timedSession.id], [timedSession.id], [timedSession.id]]);
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(ApiClient.createPlaySession).not.toHaveBeenCalled();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(ApiClient.resumePlaySessionOpponentMove).not.toHaveBeenCalled();
    expect(syncQueuedMoves).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('allows a new game and reload after consuming an unavailable query', async () => {
    window.history.replaceState(null, '', '/play?session=missing&mode=study');
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.getPlaySession).mockRejectedValueOnce(new ApiRequestError('Missing', 404));
    const active: PlaySessionRead = { id: 'new-game', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({ id: 'test-player', preferences: {} });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(active);
    const view = render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByTestId('session-info');
    expect(window.location.search).toBe('?mode=study');
    view.unmount();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(active);
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith('new-game');
  });

  it('keeps stored A reachable when explicit B is resumed and then resigned', async () => {
    window.history.replaceState(null, '', '/play?session=explicit-b&mode=study');
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'stored-a');
    const b: PlaySessionRead = { id: 'explicit-b', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(b);
    vi.mocked(ApiClient.resignPlaySession).mockResolvedValue({ ...b, status: 'completed' });
    const view = render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe('stored-a');
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    await waitFor(() => expect(window.location.search).toBe('?mode=study'));
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe('stored-a');
    view.unmount();
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...b, id: 'stored-a' });
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(ApiClient.getPlaySession).toHaveBeenLastCalledWith('stored-a');
  });

  it.each([
    { manual: 8, suggestion: 2, expected: '8' },
    { manual: null, suggestion: 8, expected: '8' },
    { manual: null, suggestion: null, expected: '1' },
  ])('preselects manual $manual before suggestion $suggestion without persisting effects', async ({ manual, suggestion, expected }) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue({
      ...onboardingFixture, manual_bot_level: manual, suggested_bot_level: suggestion,
    });
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toHaveValue(expected));
    expect(ApiClient.setPlayerBotChoice).not.toHaveBeenCalled();
  });

  it('persists only explicit manual changes and retains them after a later suggestion and reload', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const view = render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toBeEnabled());
    fireEvent.change(screen.getByTestId('bot-level-select'), { target: { value: '8' } });
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toHaveValue('8'));
    expect(ApiClient.setPlayerBotChoice).toHaveBeenCalledExactlyOnceWith('test-player', 8);
    view.unmount();
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue({
      ...onboardingFixture, manual_bot_level: 8, suggested_bot_level: 2,
    });
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toHaveValue('8'));
    expect(ApiClient.setPlayerBotChoice).toHaveBeenCalledTimes(1);
  });

  it('does not start with a guessed default when the saved bot choice cannot load', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.getPlayerOnboarding).mockRejectedValueOnce(new Error('Offline'));
    render(<PlayScreen />);
    expect(await screen.findByText('Offline')).toBeInTheDocument();
    expect(screen.getByTestId('start-btn')).toBeDisabled();
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue({ ...onboardingFixture, manual_bot_level: 8 });
    fireEvent.click(screen.getByRole('button', { name: 'Retry bot choice' }));
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    expect(screen.getByTestId('bot-level-select')).toHaveValue('8');
    expect(ApiClient.createPlaySession).not.toHaveBeenCalled();
  });

  it('keeps the previous choice after a failed manual write', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.setPlayerBotChoice).mockRejectedValueOnce(new Error('Choice was not saved'));
    render(<PlayScreen />);
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toBeEnabled());
    fireEvent.change(screen.getByTestId('bot-level-select'), { target: { value: '8' } });
    expect(await screen.findByText('Choice was not saved')).toBeInTheDocument();
    expect(screen.getByTestId('bot-level-select')).toHaveValue('1');
  });
  it('resigns immediately and links to the completed game review', async () => {
    const session: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.resignPlaySession).mockResolvedValue({ ...session, status: 'completed' });
    const onNavigate = vi.fn();
    render(<PlayScreen initialSession={session} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    expect(await screen.findByTestId('game-result')).toHaveTextContent('You resigned. Opponent wins.');
    expect(ApiClient.resignPlaySession).toHaveBeenCalledWith('sess-123');
    expect(screen.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', '/games/game-123/analysis');
    expect(screen.queryByRole('button', { name: 'Resign' })).not.toBeInTheDocument();
    await waitFor(() => expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull());
    expect(fireEvent.click(screen.getByRole('link', { name: 'Review game' }))).toBe(false);
    expect(onNavigate).toHaveBeenCalledWith('/games/game-123/analysis');
  });

  it('resigns before the first move without creating or linking an empty game', async () => {
    const session: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.resignPlaySession).mockResolvedValue({ ...session, status: 'completed' });
    render(<PlayScreen initialSession={session} />);
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    expect(await screen.findByTestId('game-result')).toHaveTextContent('You resigned. Opponent wins.');
    expect(screen.queryByRole('link', { name: 'Review game' })).not.toBeInTheDocument();
    expect(ApiClient.getPlaySession).not.toHaveBeenCalled();
    expect(ApiClient.createGame).not.toHaveBeenCalled();
  });

  it('shows a terminal result immediately and fetches the lazily created game review id', async () => {
    const session: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    const persistedSession = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockReturnValueOnce(persistedSession.promise);
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: null, status: 'completed' });
    render(<PlayScreen initialFen="7k/8/5KQ1/8/8/8/8/8 w - - 0 1" initialSession={session} />);
    await act(async () => { await getRegisteredMoveHandler()('g6', 'g7'); });
    expect(screen.getByTestId('game-result')).toHaveTextContent('Checkmate. White wins.');
    expect(ApiClient.getPlaySession).toHaveBeenCalledWith('sess-123');
    expect(screen.queryByRole('link', { name: 'Review game' })).not.toBeInTheDocument();
    await act(async () => {
      persistedSession.resolve({ ...session, status: 'completed', game_id: 'game-123' });
    });
    expect(screen.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', '/games/game-123/analysis');
  });

  it.each([
    ['g7', 'Checkmate. White wins.'],
    ['f7', 'Draw by stalemate.'],
  ])('shows the terminal result after Q%s without reloading', async (destination, result) => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: null, status: 'completed' });
    render(<PlayScreen initialFen="7k/8/5KQ1/8/8/8/8/8 w - - 0 1"
      initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }} />);
    await act(async () => { await getRegisteredMoveHandler()('g6', destination); });
    expect(await screen.findByTestId('game-result')).toHaveTextContent(result);
    expect(screen.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', '/games/game-123/analysis');
  });

  it('sends the selected under-promotion to the server', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'a1b1', status: 'active' });
    render(<PlayScreen initialFen="8/P7/8/8/8/8/8/k6K w - - 0 1"
      initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }} />);
    await act(async () => { await getRegisteredMoveHandler()('a7', 'a8'); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Promote to rook' })); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('sess-123', { move: 'a7a8r' });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('a8=R+');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Kb1');
  });

  it('shows an insufficient-material draw after bishop promotion', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: null, status: 'completed' });
    render(<PlayScreen initialFen="8/P7/8/8/8/8/8/k6K w - - 0 1"
      initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }} />);
    await act(async () => { await getRegisteredMoveHandler()('a7', 'a8'); });
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Promote to bishop' })); });
    expect(await screen.findByTestId('game-result')).toHaveTextContent('Draw.');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('sess-123', { move: 'a7a8b' });
  });


  it('starts a game and updates session info', async () => {
    vi.mocked(ApiClient.createPlayer).mockResolvedValueOnce({
      id: 'test-player',
      preferences: {},
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValueOnce({
      id: 'sess-123',
      player_id: 'test-player',
      status: 'active',
      coach_mode: false,
      assistance_modes: [],
      opponent_config: {},
    });

    render(<PlayScreen />);
    fireEvent.change(screen.getByTestId('player-id-input'), { target: { value: 'test-player' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(ApiClient.createPlayer).toHaveBeenCalledWith({ id: 'test-player', display_name: 'Anonymous' });
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith({
        player_id: 'test-player',
        opponent_config: { provider: 'stockfish', bot_level: '1' },
        coach_mode: false,
        player_color: 'white',
      });
      expect(screen.getByTestId('session-info').textContent).toContain('Status: active');
      expect(chessgroundMock.set).toHaveBeenCalledWith(
        expect.objectContaining({
          movable: expect.objectContaining({ color: 'white' }),
        })
      );
      expect(localStorage.getItem('scan64_active_play_session_id')).toBe('sess-123');
      // The setup form unmounts when the session starts, moving the board; without
      // this the cached Chessground bounds leave the board unresponsive to pointers.
      expect(chessgroundMock.redrawAll).toHaveBeenCalled();
    });
  });

  it('hides setup while restoring a saved session', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    const sessionRequest = Promise.withResolvers<PlaySessionRead>();
    vi.mocked(ApiClient.getPlaySession).mockReturnValueOnce(sessionRequest.promise);

    render(<PlayScreen />);

    expect(screen.getByTestId('resuming-game')).toBeInTheDocument();
    expect(screen.queryByTestId('start-btn')).not.toBeInTheDocument();

    await act(async () => {
      sessionRequest.resolve({ id: 'sess-123', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] });
    });

    await waitFor(() => {
      expect(screen.getByTestId('session-info')).toHaveTextContent('Status: active');
    });
  });

  it('restores an active session at its persisted game position', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123',
      pgn: '1. e4 e5 *',
      white: 'test-player',
      black: 'Stockfish',
      result: '*',
    });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledWith('sess-123');
      expect(ApiClient.getGame).toHaveBeenCalledWith('game-123');
      expect(screen.getByTestId('session-info')).toHaveTextContent('Status: active');
      expect(chessgroundMock.set).toHaveBeenCalledWith(
        expect.objectContaining({
          fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
        }),
      );
      expect(chessgroundMock.set).toHaveBeenCalledWith(
        expect.objectContaining({
          movable: expect.objectContaining({
            color: 'white',
            dests: expect.any(Map),
          }),
        }),
      );
    });
    expect(setActivePlayerId).toHaveBeenCalledWith('test-player');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(ApiClient.getGame).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/queued move|synchronizing game/i)).not.toBeInTheDocument();
  });

  it('refreshes a reconciled offline human move and exposes only explicit bot recovery', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      player_color: 'white', game_id: 'game-123', opponent_config: {},
      status: 'active', coach_mode: false, assistance_modes: [] };
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValueOnce(new Error('response lost'));
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(playSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '1. e4 *', white: 'test-player', black: 'Stockfish', result: '*',
    });
    vi.mocked(ApiClient.resumePlaySession).mockResolvedValue({
      opponent_move: 'e7e5', status: 'active',
    });
    vi.mocked(syncQueuedMoves).mockImplementation(async () => {
      window.dispatchEvent(new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, { detail: {
        queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn: '' },
        response: null,
      } }));
    });
    render(<PlayScreen initialSession={playSession} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(queueMove).toHaveBeenCalledWith('sess-123', 'e2e4', new Chess().pgn());
    network.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    const resume = await screen.findByRole('button', { name: 'Resume bot turn' });
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.resumePlaySession).not.toHaveBeenCalled();
    fireEvent.click(resume);
    await waitFor(() => expect(ApiClient.resumePlaySession).toHaveBeenCalledExactlyOnceWith('sess-123', 'test-player'));
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5'));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    network.mockRestore();
  });

  it('keeps the bot-play-unavailable notice through the refresh and on bot recovery', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      player_color: 'white', game_id: 'game-123', opponent_config: {},
      status: 'active', coach_mode: false, assistance_modes: [] };
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(playSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '1. e4 *', white: 'test-player', black: 'Stockfish', result: '*',
    });
    const { BotPlayUnavailableError } = await import('../api/client');
    vi.mocked(ApiClient.resumePlaySession).mockRejectedValue(new BotPlayUnavailableError('no Skill Level'));
    vi.mocked(syncQueuedMoves).mockImplementation(async () => {
      window.dispatchEvent(new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, { detail: {
        queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn: '' },
        response: null,
        botPlayUnavailable: 'Bot play is unavailable under the active engine profile. Switch to the Stockfish profile to play the bot.',
      } }));
    });
    render(<PlayScreen initialSession={playSession} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    network.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    const resume = await screen.findByRole('button', { name: 'Resume bot turn' });
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e4'));
    expect(screen.getByRole('alert')).toHaveTextContent(/Bot play is unavailable under the active engine profile/);
    fireEvent.click(resume);
    await waitFor(() => expect(ApiClient.resumePlaySession).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getAllByRole('alert')).toHaveLength(1));
    expect(screen.getByRole('alert')).toHaveTextContent(/active engine profile/);
    expect(screen.queryByText(/your move was not replayed/)).not.toBeInTheDocument();
    network.mockRestore();
  });

  it('locks moves and resign through queued acknowledgment, failed refresh and canonical retry', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    const canonicalGame = { id: 'game-123', pgn: '1. e4 e5 *',
      white: 'test-player', black: 'Stockfish', result: '*' };
    const refresh = Promise.withResolvers<typeof canonicalGame>();
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ opponent_move: 'b8c6', status: 'active' });
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(playSession);
    vi.mocked(ApiClient.getGame).mockReturnValueOnce(refresh.promise).mockResolvedValueOnce(canonicalGame);
    vi.mocked(syncQueuedMoves).mockImplementation(async () => {
      window.dispatchEvent(new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, { detail: {
        queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 },
        response: { opponent_move: 'e7e5', status: 'active' },
      } }));
    });
    render(<PlayScreen initialSession={playSession} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(queueMove).toHaveBeenCalledWith('sess-123', 'e2e4', new Chess().pgn());
    network.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalledTimes(1));
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Hint' })).toBeDisabled();
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(ApiClient.resignPlaySession).not.toHaveBeenCalled();

    await act(async () => { refresh.reject(new Error('canonical refresh unavailable')); });
    await screen.findByTestId('retry-game-refresh');
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId('retry-game-refresh'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false');
    expect(screen.getByTestId('takeback-btn')).toBeEnabled();
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenLastCalledWith('sess-123', { move: 'g1f3' });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Nc6');
    network.mockRestore();
  });

  it('ignores an older synchronization finishing after a newer refresh and live move', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    const olderQueueRead = Promise.withResolvers<QueuedMove[]>();
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce({ opponent_move: 'a7a6', status: 'active' });
    vi.mocked(getQueuedMoves).mockReturnValueOnce(olderQueueRead.promise).mockResolvedValue([]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(playSession);
    vi.mocked(ApiClient.getGame)
      .mockResolvedValueOnce({ id: 'game-123', pgn: '1. e4 e5 2. Nf3 Nc6 *',
        white: 'test-player', black: 'Stockfish', result: '*' })
      .mockResolvedValueOnce({ id: 'game-123', pgn: '1. e4 e5 *',
        white: 'test-player', black: 'Stockfish', result: '*' });
    vi.mocked(syncQueuedMoves).mockImplementationOnce(async () => {
      window.dispatchEvent(new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, { detail: {
        queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 },
        response: { opponent_move: 'e7e5', status: 'active' },
      } }));
    }).mockResolvedValue(undefined);
    render(<PlayScreen initialSession={playSession} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    network.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await waitFor(() => expect(getQueuedMoves).toHaveBeenCalledTimes(1));
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
    await act(async () => { await getRegisteredMoveHandler()('f1', 'b5'); });
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Bb5');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('a6');

    await act(async () => { olderQueueRead.resolve([]); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(ApiClient.getGame).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Bb5');
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('a6');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
    network.mockRestore();
  });

  it('refreshes canonical state after synchronizing a queued resumed move', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(getQueuedMoves)
      .mockResolvedValueOnce([{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1 }])
      .mockResolvedValueOnce([]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] });
    vi.mocked(ApiClient.getGame)
      .mockResolvedValueOnce({
        id: 'game-123',
        pgn: '1. e4 e5 *',
        white: 'test-player',
        black: 'Stockfish',
        result: '*',
      })
      .mockResolvedValueOnce({
        id: 'game-123',
        pgn: '1. e4 e5 2. Nf3 Nc6 *',
        white: 'test-player',
        black: 'Stockfish',
        result: '*',
      });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(syncQueuedMoves).toHaveBeenCalled();
    });


    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
      expect(ApiClient.getGame).toHaveBeenCalledTimes(2);
      expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Nf3');
      expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('Nc6');
      expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
    });
  });


  it('keeps a resumed board locked while a queued move remains', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(getQueuedMoves).mockResolvedValue([
      { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 },
    ]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123',
      pgn: '1. e4 e5 *',
      white: 'test-player',
      black: 'Stockfish',
      result: '*',
    });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(screen.getByTestId('retry-move-sync')).toBeInTheDocument();
    });
    const move = await waitFor(getRegisteredMoveHandler);
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
    await act(async () => { await move('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it.each([false, true])(
    'confirms legacy discard without removing recorded or other-session moves (recorded=%s)',
    async retainRecorded => {
      localStorage.setItem('scan64_player_id', 'test-player');
      localStorage.setItem('scan64_active_play_session_id', 'sess-123');
      const legacy = { sessionId: 'sess-123', move: 'g1f3', timestamp: 1 };
      const other = { sessionId: 'other-session', move: 'd2d4', timestamp: 2 };
      const recorded = { sessionId: 'sess-123', move: 'b1c3', timestamp: 3, sourcePgn: '1. e4 e5 *' };
      let queued: QueuedMove[] = [legacy, other, ...(retainRecorded ? [recorded] : [])];
      vi.mocked(getQueuedMoves).mockImplementation(async () => queued);
      vi.mocked(removeQueuedMove).mockImplementation(async (sessionId, move, timestamp) => {
        queued = queued.filter(entry =>
          entry.sessionId !== sessionId || entry.move !== move || entry.timestamp !== timestamp,
        );
      });
      const resumed: PlaySessionRead = { ...assistanceSession, id: 'sess-123' };
      const refreshed = Promise.withResolvers<PlaySessionRead>();
      vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(resumed).mockReturnValue(refreshed.promise);
      vi.mocked(ApiClient.getGame).mockResolvedValue({
        id: 'game-123', pgn: '1. e4 e5 *', white: 'test-player', black: 'Stockfish', result: '*',
      });
      render(<PlayScreen />);
      const discard = await screen.findByRole('button', { name: 'Discard legacy queued moves…' });
      fireEvent.click(discard);
      expect(screen.getByRole('region', { name: 'Confirm legacy move discard' })).toHaveTextContent(
        'Unsent moves will be lost',
      );
      expect(removeQueuedMove).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Keep queued moves' }));
      expect(removeQueuedMove).not.toHaveBeenCalled();
      expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
      fireEvent.click(discard);
      fireEvent.click(screen.getByRole('button', { name: 'Discard and refresh' }));
      await waitFor(() => expect(removeQueuedMove).toHaveBeenCalledExactlyOnceWith('sess-123', 'g1f3', 1));
      expect(queued).toEqual([other, ...(retainRecorded ? [recorded] : [])]);
      expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
      expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
      await act(async () => getRegisteredMoveHandler()('g1', 'f3'));
      expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
      if (retainRecorded) {
        expect(screen.getByTestId('retry-move-sync')).toBeInTheDocument();
        expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
      } else {
        await waitFor(() => expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2));
        await act(async () => refreshed.resolve(resumed));
        await waitFor(() => expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled());
        expect(screen.queryByTestId('retry-move-sync')).not.toBeInTheDocument();
        expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
      }
    },
  );

  it('retains legacy queued moves when the actor changes after confirmation opens', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(getQueuedMoves).mockResolvedValue([{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1 }]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...assistanceSession, id: 'sess-123' });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '', white: 'test-player', black: 'Stockfish', result: '*',
    });
    render(<PlayScreen />);
    fireEvent.click(await screen.findByRole('button', { name: 'Discard legacy queued moves…' }));
    localStorage.setItem('scan64_player_id', 'other-player');
    fireEvent.click(screen.getByRole('button', { name: 'Discard and refresh' }));
    expect(removeQueuedMove).not.toHaveBeenCalled();
    expect(screen.getByRole('alert')).toHaveTextContent('Return to the game owner');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
  });

  it('does not unlock or restore an old actor after a discarded queue waits for canonical refresh', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    let queued: QueuedMove[] = [{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1 }];
    vi.mocked(getQueuedMoves).mockImplementation(async () => queued);
    vi.mocked(removeQueuedMove).mockImplementation(async () => { queued = []; });
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...assistanceSession, id: 'sess-123' });
    const canonical = Promise.withResolvers<GameRead>();
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce({
      id: 'game-123', pgn: '', white: 'test-player', black: 'Stockfish', result: '*',
    }).mockReturnValue(canonical.promise);
    render(<PlayScreen />);
    fireEvent.click(await screen.findByRole('button', { name: 'Discard legacy queued moves…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Discard and refresh' }));
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalledTimes(2));
    const activePlayerCalls = vi.mocked(setActivePlayerId).mock.calls.length;
    localStorage.setItem('scan64_player_id', 'other-player');
    await act(async () => canonical.resolve({
      id: 'game-123', pgn: '1. e4 e5 *', white: 'test-player', black: 'Stockfish', result: '*',
    }));
    expect(setActivePlayerId).toHaveBeenCalledTimes(activePlayerCalls);
    expect(localStorage.getItem('scan64_player_id')).toBe('other-player');
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
    await act(async () => getRegisteredMoveHandler()('d2', 'd4'));
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    const refresh = screen.getByTestId('retry-game-refresh');
    const sessionReads = vi.mocked(ApiClient.getPlaySession).mock.calls.length;
    fireEvent.click(refresh);
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(sessionReads);
    expect(localStorage.getItem('scan64_player_id')).toBe('other-player');
    expect(screen.getByRole('alert')).toHaveTextContent('Return to the game owner');
    localStorage.setItem('scan64_player_id', 'test-player');
    fireEvent.click(refresh);
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
      expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('e5');
    });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
  });

  it.each([undefined, ''])('keeps retained queue intent locked after owner recovery (source=%s)', async sourcePgn => {
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    const queued = [{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn }];
    vi.mocked(getQueuedMoves).mockResolvedValue(queued);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ ...assistanceSession, id: 'sess-123' });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '', white: 'test-player', black: 'Stockfish', result: '*',
    });
    render(<PlayScreen />);
    const retry = await screen.findByTestId('retry-move-sync');
    localStorage.setItem('scan64_player_id', 'other-player');
    fireEvent.click(retry);
    const refresh = screen.getByTestId('retry-game-refresh');
    localStorage.setItem('scan64_player_id', 'test-player');
    await act(async () => { fireEvent.click(refresh); });
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('button', { name: 'Resign' })).toBeDisabled();
    expect(removeQueuedMove).not.toHaveBeenCalled();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
  });

  it('retries a failed canonical refresh after queued-move synchronization', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(getQueuedMoves)
      .mockResolvedValueOnce([{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1 }])
      .mockResolvedValueOnce([]);
    const resumedSession = { id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active' as const, coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.getPlaySession)
      .mockResolvedValueOnce(resumedSession)
      .mockRejectedValueOnce(new Error('temporary failure'))
      .mockResolvedValueOnce(resumedSession);
    vi.mocked(ApiClient.getGame)
      .mockResolvedValueOnce({
        id: 'game-123',
        pgn: '1. e4 e5 *',
        white: 'test-player',
        black: 'Stockfish',
        result: '*',
      })
      .mockResolvedValueOnce({
        id: 'game-123',
        pgn: '1. e4 e5 2. Nf3 Nc6 *',
        white: 'test-player',
        black: 'Stockfish',
        result: '*',
      });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
    });

    await waitFor(() => {
      expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('retry-game-refresh'));

    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(3);
      expect(screen.queryByTestId('retry-game-refresh')).not.toBeInTheDocument();
      expect(chessgroundMock.set).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fen: 'r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3',
          movable: expect.objectContaining({ color: 'white', dests: expect.any(Map) }),
        }),
      );
    });
  });

  it('clears an ordinary terminal saved session without loading its game or result', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'ordinary-ended-game',
      opponent_config: {}, status: 'completed', coach_mode: false, assistance_modes: [] });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledWith('sess-123');
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
    });
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(screen.queryByTestId('session-info')).not.toBeInTheDocument();
    expect(screen.queryByTestId('game-result')).not.toBeInTheDocument();
    expect(screen.getByTestId('start-btn')).toBeInTheDocument();
  });

  it('removes an unresolvable saved session and reports its absence', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockRejectedValue(
      new ApiRequestError('Not Found', 404),
    );

    render(<PlayScreen />);

    await waitFor(() => {
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
      expect(screen.getByText('Previous game is no longer available.')).toBeInTheDocument();
    });
  });

  it('removes an invalid saved session identifier', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'not-a-session-id');
    vi.mocked(ApiClient.getPlaySession).mockRejectedValue(
      new ApiRequestError('Unprocessable Content', 422),
    );

    render(<PlayScreen />);

    await waitFor(() => {
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
      expect(screen.getByText('Previous game is no longer available.')).toBeInTheDocument();
    });
  });

  it('retains a saved session when its game lookup fails', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] });
    vi.mocked(ApiClient.getGame).mockRejectedValue(
      new ApiRequestError('Not Found', 404),
    );

    render(<PlayScreen />);

    await waitFor(() => {
      expect(screen.getByText('Unable to resume game: Not Found')).toBeInTheDocument();
    });
    expect(localStorage.getItem('scan64_active_play_session_id')).toBe('sess-123');
  });

  it('submits a board move from an initial play session', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: null,
      status: 'active',
    });

    render(
      <PlayScreen
        initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }}
      />
    );

    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });
    const after = chessgroundMock.after;
    if (!after) {
      throw new Error('Chessground move handler was not registered');
    }

    await act(async () => {
      await after('e2', 'e4');
    });

    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('sess-123', {
      move: 'e2e4',
    });
  });

  it('keeps a queued coach interruption visible and the board locked until review completes', async () => {
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValueOnce(new Error('offline'));
    vi.mocked(queueMove).mockResolvedValue();
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
      opponent_config: {}, status: 'active', coach_mode: true, assistance_modes: [],
    });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '1. e4 e5 *', white: 'test-player', black: 'Stockfish', result: '*',
    });

    render(
      <PlayScreen
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
          coach_mode: true,
          assistance_modes: [],
        }}
      />,
    );
    await act(async () => {
      await getRegisteredMoveHandler()('e2', 'e4');
    });
    await waitFor(() => {
      expect(queueMove).toHaveBeenCalledWith('sess-123', 'e2e4', new Chess().pgn());
    });

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, {
          detail: {
            queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 },
            response: {
              opponent_move: 'e7e5',
              status: 'active',
              critical_interruption: {
                lesson: serverLesson,
                opportunity_id: 'opportunity-123',
                study_session_id: 'study-123',
              },
            },
          },
        }),
      );
    });

    expect(screen.getByTestId('critical-moment-review')).toBeInTheDocument();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );

    await act(async () => {
      window.dispatchEvent(
        new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, {
          detail: {
            queuedMove: { sessionId: 'sess-123', move: 'e2e4', timestamp: 2 },
            response: { opponent_move: null, status: 'active', critical_interruption: null },
          },
        }),
      );
    });

    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );
    network.mockReturnValue(true);
    await act(async () => { window.dispatchEvent(new Event('online')); });
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByTestId('complete-critical-review'));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: 'white', dests: expect.any(Map) }),
      }),
    );
    network.mockRestore();
  });

  it('retains a queued interruption when the canonical board needs refresh', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    const queuedMove = { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 };
    const resumedSession = {
      id: 'sess-123',
      player_id: 'test-player',
      game_id: 'game-123',
      opponent_config: {},
      status: 'active' as const,
      coach_mode: true,
      assistance_modes: [],
    };
    vi.mocked(getQueuedMoves).mockResolvedValueOnce([queuedMove]).mockResolvedValueOnce([]);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(resumedSession);
    vi.mocked(ApiClient.getGame)
      .mockResolvedValueOnce({
        id: 'game-123',
        pgn: '',
        white: 'test-player',
        black: 'Stockfish',
        result: '*',
      })
      .mockRejectedValueOnce(new Error('network unavailable'));
    vi.mocked(syncQueuedMoves).mockImplementation(async () => {
      window.dispatchEvent(
        new CustomEvent(QUEUED_MOVE_SYNC_SUCCEEDED, {
          detail: {
            queuedMove,
            response: {
              opponent_move: 'e7e5',
              status: 'active',
              critical_interruption: {
                lesson: serverLesson,
                opportunity_id: 'opportunity-123',
                study_session_id: 'study-123',
              },
            },
          },
        }),
      );
    });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(screen.getByTestId('critical-moment-review')).toBeInTheDocument();
      expect(chessgroundMock.set).toHaveBeenLastCalledWith(
        expect.objectContaining({
          movable: expect.objectContaining({ color: undefined, dests: undefined }),
        }),
      );
    });
    fireEvent.click(screen.getByTestId('complete-critical-review'));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );
  });

  it('keeps a coach interruption locked while an online canonical refresh is pending', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: 'e2e4',
      status: 'active',
      critical_interruption: {
        lesson: serverLesson,
        opportunity_id: 'opportunity-123',
        study_session_id: 'study-123',
      },
    });

    render(
      <PlayScreen
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
          coach_mode: true,
          assistance_modes: [],
        }}
      />,
    );
    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });

    await act(async () => {
      await getRegisteredMoveHandler()('e2', 'e4');
    });

    expect(screen.getByTestId('retry-game-refresh')).toBeInTheDocument();
    expect(screen.getByTestId('critical-moment-review')).toBeInTheDocument();
    fireEvent.click(screen.getByTestId('complete-critical-review'));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined }),
      }),
    );
  });

  it('marks the session completed after an opponent checkmate', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: 'd8h4',
      status: 'completed',
    });
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');

    render(
      <PlayScreen
        initialFen="rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2"
        initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }}
      />,
    );

    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });

    await act(async () => {
      await getRegisteredMoveHandler()('g2', 'g4');
    });

    expect(screen.getByTestId('session-info')).toHaveTextContent('Status: completed');
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined }),
      }),
    );
    await waitFor(() => {
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
    });
  });

  it('restores Chessground to the player turn after an opponent response', async () => {
    vi.mocked(ApiClient.makePlaySessionMove)
      .mockResolvedValueOnce({ opponent_move: 'e7e5', status: 'active' })
      .mockResolvedValueOnce({ opponent_move: 'b8c6', status: 'active' });

    render(
      <PlayScreen
        initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }}
      />,
    );

    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });
    const after = getRegisteredMoveHandler();

    await act(async () => {
      await after('e2', 'e4');
    });

    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        turnColor: 'white',
        movable: expect.objectContaining({ color: 'white' }),
      }),
    );

    await act(async () => {
      await after('g1', 'f3');
    });

    expect(ApiClient.makePlaySessionMove).toHaveBeenNthCalledWith(2, 'sess-123', {
      move: 'g1f3',
    });
  });

  it('preserves the confirmed board after an invalid local move', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: 'e7e5',
      status: 'active',
    });

    render(
      <PlayScreen
        initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }}
      />,
    );

    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });
    const after = getRegisteredMoveHandler();

    await act(async () => {
      await after('e2', 'e4');
    });
    chessgroundMock.set.mockClear();

    await act(async () => {
      await after('e2', 'e5');
    });

    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
        turnColor: 'white',
        movable: expect.objectContaining({ color: 'white' }),
      }),
    );
  });

  it('renders only a server-created interruption in coach mode', async () => {
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({
      id: 'test-player',
      preferences: {},
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({
      id: 'sess-123',
      player_id: 'test-player',
      status: 'active',
      opponent_config: {},
      coach_mode: true,
      assistance_modes: [],
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: null,
      status: 'active',
      critical_interruption: {
        lesson: serverLesson,
        opportunity_id: 'opportunity-123',
        study_session_id: 'study-123',
      },
    });

    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('coach-mode-toggle'));
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByTestId('session-info');
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    const move = getRegisteredMoveHandler();
    await act(async () => {
      await move('e2', 'e4');
    });

    expect(ApiClient.createPlaySession).toHaveBeenCalledWith(
      expect.objectContaining({
        opponent_config: { provider: 'stockfish', bot_level: '1' },
        coach_mode: true,
      }),
    );
    expect(screen.getByTestId('critical-moment-review')).toHaveAttribute(
      'data-lesson-id',
      'lesson-123',
    );
    expect(screen.getByTestId('critical-moment-review')).toHaveAttribute(
      'data-opportunity-id',
      'opportunity-123',
    );
    expect(screen.getByTestId('critical-moment-review')).toHaveAttribute(
      'data-study-session-id',
      'study-123',
    );
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );
    fireEvent.click(screen.getByTestId('complete-critical-review'));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: 'black', dests: expect.any(Map) }),
      }),
    );
  });

  it('does not render an interruption when coach mode receives no server context', async () => {
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({
      id: 'test-player',
      preferences: {},
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({
      id: 'sess-123',
      player_id: 'test-player',
      status: 'active',
      coach_mode: true,
      assistance_modes: [],
      opponent_config: {},
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: null,
      status: 'active',
      critical_interruption: null,
    });

    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('coach-mode-toggle'));
    fireEvent.click(screen.getByTestId('start-btn'));
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    await act(async () => {
      await getRegisteredMoveHandler()('e2', 'e4');
    });

    expect(screen.queryByTestId('critical-moment-review')).toBeNull();
  });

  it('ignores interruption context on a non-coach session', async () => {
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({
      id: 'test-player',
      preferences: {},
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({
      id: 'sess-123',
      player_id: 'test-player',
      status: 'active',
      coach_mode: false,
      assistance_modes: [],
      opponent_config: {},
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: null,
      status: 'active',
      critical_interruption: {
        lesson: serverLesson,
        opportunity_id: 'unexpected-opportunity',
        study_session_id: 'unexpected-study-session',
      },
    });

    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByTestId('session-info');
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    await act(async () => {
      await getRegisteredMoveHandler()('e2', 'e4');
    });

    expect(screen.queryByTestId('critical-moment-review')).toBeNull();
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: 'black', dests: expect.any(Map) }),
      }),
    );
  });

  it('allows a black-to-move initial position', async () => {
    render(
      <PlayScreen
        initialFen="rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }}
      />
    );

    await waitFor(() => {
      expect(chessgroundMock.color).toBe('black');
    });
  });

  it('shows error message if API fails', async () => {
    vi.mocked(ApiClient.createPlayer).mockRejectedValueOnce(new Error('Network Error'));

    render(<PlayScreen />);
    fireEvent.change(screen.getByTestId('player-id-input'), { target: { value: 'test-player' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(screen.getByText('Network Error')).toBeTruthy();
    });
  });
  it('uses server bot names and sends selected level and color, applying Black opening move', async () => {
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({ id: 'test-player', preferences: {} });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({ id: 'black-start', player_id: 'test-player',
      opponent_config: { player_color: 'black' }, player_color: 'black', opponent_move: 'e2e4',
      status: 'active', coach_mode: false, assistance_modes: [] });
    render(<PlayScreen />);
    await screen.findByRole('option', { name: '8. Server expert' });
    fireEvent.change(screen.getByTestId('player-id-input'), { target: { value: 'test-player' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.change(screen.getByTestId('bot-level-select'), { target: { value: '8' } });
    await waitFor(() => expect(screen.getByTestId('bot-level-select')).toHaveValue('8'));
    fireEvent.change(screen.getByTestId('player-color-select'), { target: { value: 'black' } });
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByTestId('session-info');
    expect(ApiClient.createPlaySession).toHaveBeenCalledWith({ player_id: 'test-player',
      opponent_config: { provider: 'stockfish', bot_level: '8' }, player_color: 'black', coach_mode: false });
    await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      fen: expect.stringContaining('4P3'), movable: expect.objectContaining({ color: 'black' }) })));
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
    expect(screen.getByRole('status', { name: 'Game position' })).toHaveTextContent('Last move: e4. Black to move.');
  });

  it('offers random color without guessing the server chosen color', async () => {
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({ id: 'random-start', player_id: 'test-player',
      opponent_config: {}, player_color: 'black', opponent_move: 'd2d4', status: 'active', coach_mode: false, assistance_modes: [] });
    render(<PlayScreen />);
    fireEvent.change(screen.getByTestId('player-id-input'), { target: { value: 'test-player' } });
    fireEvent.change(screen.getByTestId('player-color-select'), { target: { value: 'random' } });
    await waitFor(() => expect(screen.getByTestId('start-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('start-btn'));
    await screen.findByTestId('session-info');
    expect(ApiClient.createPlaySession).toHaveBeenCalledWith(expect.objectContaining({ player_color: 'random' }));
    await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black' })));
  });

  it('reloads persisted Black orientation and permits only human moves and resignation', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'reload-black');
    const session: PlaySessionRead = { id: 'reload-black', player_id: 'test-player', game_id: 'black-game',
      opponent_config: { player_color: 'black' }, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(session);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'black-game', pgn: '1. e4', white: 'bot', black: 'human', result: '*' });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'g1f3', status: 'active' });
    vi.mocked(ApiClient.resignPlaySession).mockResolvedValue({ ...session, status: 'completed' });
    render(<PlayScreen />);
    await screen.findByTestId('session-info');
    await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({
      orientation: 'black', movable: expect.objectContaining({ color: 'black' }) })));
    await act(async () => { await getRegisteredMoveHandler()('e7', 'e5'); });
    await waitFor(() => expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('reload-black', { move: 'e7e5' }));
    fireEvent.click(screen.getByRole('button', { name: 'Resign' }));
    await screen.findByTestId('game-result');
    expect(ApiClient.resignPlaySession).toHaveBeenCalledWith('reload-black');
  });

  it('allows only Black premoves, not bot moves, while an explicit Black session awaits the bot', async () => {
    render(<PlayScreen initialSession={{ id: 'black-wait', player_id: 'test-player', opponent_config: {},
      player_color: 'black', status: 'active', coach_mode: false, assistance_modes: [] }} />);
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      movable: expect.objectContaining({ color: 'black', dests: undefined }), premovable: { enabled: true } }));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
  });

  it('takes back a pair and reloads the authoritative game', async () => {
    const session: PlaySessionRead = { id: 'undo', player_id: 'test-player', game_id: 'undo-game',
      player_color: 'white', opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active' });
    vi.mocked(ApiClient.takebackPlaySession).mockResolvedValue(session);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ id: 'undo-game', pgn: '', white: 'human', black: 'bot', result: '*' });
    render(<PlayScreen initialSession={session} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('takeback-btn'));
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalledWith('undo-game'));
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeDisabled());
    expect(ApiClient.takebackPlaySession).toHaveBeenCalledWith('undo');
    await waitFor(() => expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({
      fen: expect.stringContaining('PPPPPPPP/RNBQKBNR w'), orientation: 'white' })));
  });

  it('refuses takeback when an offline move is queued, in both UI and handler', async () => {
    const session: PlaySessionRead = { id: 'queued-undo', player_id: 'test-player', game_id: 'queued-game',
      opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] };
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active' });
    render(<PlayScreen initialSession={session} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeEnabled());
    vi.mocked(getQueuedMoves).mockResolvedValue([{ sessionId: session.id, move: 'g1f3', timestamp: 1 }]);
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(await screen.findByText('Synchronize queued moves before taking back.')).toBeInTheDocument();
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    const offline = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new Error('offline'));
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeDisabled());
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    offline.mockRestore();
  });

  it('does not offer takeback in coach mode', () => {
    render(<PlayScreen initialSession={{ id: 'coach-undo', player_id: 'test-player', opponent_config: {},
      status: 'active', coach_mode: true, assistance_modes: [] }} />);
    expect(screen.queryByTestId('takeback-btn')).not.toBeInTheDocument();
  });

  it('disables takeback after a live offline move is queued', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({ opponent_move: 'e7e5', status: 'active' });
    render(<PlayScreen initialSession={{ id: 'live-offline', player_id: 'test-player',
      opponent_config: {}, status: 'active', coach_mode: false, assistance_modes: [] }} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeEnabled());
    const offline = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValueOnce(new Error('offline'));
    vi.mocked(queueMove).mockResolvedValue();
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    await waitFor(() => expect(queueMove).toHaveBeenCalledWith('live-offline', 'g1f3', expect.stringContaining('1. e4 e5 *')));
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    offline.mockRestore();
  });

});
