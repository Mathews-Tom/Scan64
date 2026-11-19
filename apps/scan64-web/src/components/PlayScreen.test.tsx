import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PlayScreen } from './PlayScreen';
import { ApiClient, ApiRequestError, setActivePlayerId } from '../api/client';
import type { LessonSpec, PlayClockRead, PlaySessionRead } from '../api/types';
import { onboardingFixture } from './strength.test-fixtures';
import {
  getQueuedMoves,
  QUEUED_MOVE_SYNC_SUCCEEDED,
  queueMove,
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
  ApiClient: {
    getBots: vi.fn().mockResolvedValue([{ level: 1, name: 'Server beginner', description: 'Friendly bot' }]),
    getPlayerOnboarding: vi.fn(),
    setPlayerBotChoice: vi.fn(),
    takebackPlaySession: vi.fn(),
    createPlayer: vi.fn(),
    createGame: vi.fn(),
    createPlaySession: vi.fn(),
    makePlaySessionMove: vi.fn(),
    resignPlaySession: vi.fn(),
    resumePlaySessionOpponentMove: vi.fn(),
    getGame: vi.fn(),
    getPlaySession: vi.fn(),
    getTrainingSession: vi.fn().mockResolvedValue({ session_id: 'study-1', lessons: [], move_choices: {}, attempts_used: {} }),
    recordLessonAttempt: vi.fn(),
  },
  setActivePlayerId: vi.fn(),
  getStoredPlayerId: () => localStorage.getItem('scan64_player_id'),
}));

const timedClock: PlayClockRead = {
  initial_seconds: 180, increment_seconds: 2, white_remaining_ms: 180000, black_remaining_ms: 180000,
  active_color: 'white', deadline_at: '2026-11-19T12:03:00Z', server_now: '2026-11-19T12:00:00Z',
  version: 4, terminal_reason: null, flagged_color: null,
};
const timedSession: PlaySessionRead = {
  id: 'timed-session', player_id: 'test-player', game_id: 'timed-game', player_color: 'white',
  opponent_config: {}, status: 'active', coach_mode: false, clock: timedClock,
};
const timedGame = { id: 'timed-game', pgn: '', white: 'test-player', black: 'Stockfish', result: '*' };

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('PlayScreen', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/play');
    vi.resetAllMocks();
    vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(onboardingFixture);
    vi.mocked(ApiClient.setPlayerBotChoice).mockImplementation(async (_playerId, botLevel) => ({
      ...onboardingFixture, manual_bot_level: botLevel, preferred_bot_level: botLevel,
    }));
    vi.mocked(ApiClient.getBots).mockResolvedValue([
      { level: 1, name: 'Server beginner', description: 'Friendly bot' },
      { level: 8, name: 'Server expert', description: 'Expert bot' },
    ]);
    localStorage.clear();
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

  it('offers only supported clock presets and posts a numeric control', async () => {
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

  it.each([false, true])('starts a durable bot-first timed turn and exposes a failed command retry: %s', async (failsFirst) => {
    localStorage.setItem('scan64_player_id', 'test-player');
    const pending = { ...timedSession, player_color: 'black' as const, opponent_move: null,
      clock: { ...timedClock, version: 0 } };
    const after = { ...pending, opponent_move: 'e2e4',
      clock: { ...timedClock, version: 1, active_color: 'black' as const } };
    const opponent = Promise.withResolvers<PlaySessionRead>();
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
    await act(async () => { opponent.resolve(after); });
    await waitFor(() => expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active'));
    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    }));
    expect(screen.queryByRole('button', { name: 'Retry opponent move' })).not.toBeInTheDocument();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('uses authoritative increments and the latest exact clock version for each human move', async () => {
    const after = { ...timedSession, clock: { ...timedClock, version: 6, white_remaining_ms: 181000, black_remaining_ms: 179000 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValue(after);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 *' });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'e7e5', status: 'active', clock: after.clock });
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'false'));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveTextContent('3:01'));
    expect(screen.getByTestId('black-clock')).toHaveTextContent('2:59');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('timed-session', { move: 'e2e4', expected_clock_version: 4 });
    expect(ApiClient.getGame).toHaveBeenCalledTimes(2);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 2. Nf3 Nc6 *' });
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    expect(ApiClient.makePlaySessionMove).toHaveBeenLastCalledWith('timed-session', { move: 'g1f3', expected_clock_version: 6 });
    expect(queueMove).not.toHaveBeenCalled();
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
  });

  it('reconciles an accepted move after engine failure and resumes only the durable bot version', async () => {
    const pending = { ...timedSession, clock: { ...timedClock, version: 5, active_color: 'black' as const, white_remaining_ms: 181000 } };
    const after = { ...timedSession, clock: { ...timedClock, version: 6 } };
    const opponent = Promise.withResolvers<PlaySessionRead>();
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
    await act(async () => { opponent.resolve(after); });
    await waitFor(() => expect(screen.getByTestId('white-clock')).toHaveAccessibleName('White clock, active'));
    expect(ApiClient.resumePlaySessionOpponentMove).toHaveBeenCalledTimes(1);
    expect(chessgroundMock.set).toHaveBeenCalledWith(expect.objectContaining({
      fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2',
    }));
  });

  it('refreshes a stale rejected move without replaying its version or its optimistic board', async () => {
    const after = { ...timedSession, clock: { ...timedClock, version: 8 } };
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
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(timedSession);
    vi.mocked(ApiClient.getGame).mockResolvedValue(timedGame);
    render(<PlayScreen initialSession={timedSession} />);
    await waitFor(() => expect(ApiClient.getGame).toHaveBeenCalled());
    expect(screen.getByText(/Timed moves require a connection/)).toBeInTheDocument();
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(queueMove).not.toHaveBeenCalled();
    expect(screen.getByTestId('chessground-board')).toHaveAttribute('aria-busy', 'true');
  });

  it('resumes a remounted bot turn once per version and offers an explicit failed-command retry', async () => {
    localStorage.setItem('scan64_active_play_session_id', timedSession.id);
    const pending = { ...timedSession, clock: { ...timedClock, version: 5, active_color: 'black' as const } };
    const after = { ...timedSession, clock: { ...timedClock, version: 6 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue(pending);
    vi.mocked(ApiClient.getGame).mockResolvedValue({ ...timedGame, pgn: '1. e4 *' });
    vi.mocked(ApiClient.resumePlaySessionOpponentMove).mockRejectedValueOnce(new Error('Engine unavailable')).mockResolvedValue(after);
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

  it('animates only the active side and refreshes once at zero without inventing a result', async () => {
    vi.useFakeTimers();
    let now = 0;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    const black = { ...timedSession, player_color: 'black' as const, clock: {
      ...timedClock, active_color: 'black' as const, black_remaining_ms: 2500,
    } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(black).mockResolvedValue({
      ...black, clock: { ...black.clock, black_remaining_ms: 0 },
    });
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
    view.unmount();
  });

  it('keeps a premove pending until the canonical reply and then uses its new clock version', async () => {
    const response = Promise.withResolvers<{ opponent_move: string; status: string; clock: PlayClockRead }>();
    const canonical = Promise.withResolvers<typeof timedGame>();
    const after = { ...timedSession, clock: { ...timedClock, version: 6 } };
    const next = { ...timedSession, clock: { ...timedClock, version: 8 } };
    vi.mocked(ApiClient.getPlaySession).mockResolvedValueOnce(timedSession).mockResolvedValueOnce(after).mockResolvedValue(next);
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce(timedGame).mockReturnValueOnce(canonical.promise)
      .mockResolvedValue({ ...timedGame, pgn: '1. e4 e5 2. Nf3 Nc6 *' });
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
    await act(async () => { canonical.resolve({ ...timedGame, pgn: '1. e4 e5 *' }); await firstMove; });
    await waitFor(() => expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(2));
    expect(ApiClient.makePlaySessionMove).toHaveBeenLastCalledWith('timed-session', { move: 'g1f3', expected_clock_version: 6 });
  });

  it('never queues an ambiguously accepted timed move when the connection drops during the request', async () => {
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
    const pending = { ...timedSession, clock: { ...timedClock, version: 5, active_color: 'black' as const } };
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
    expect(screen.getByRole('alert')).toHaveTextContent('Connection lost after submission');
    expect(screen.getByTestId('black-clock')).toHaveAccessibleName('Black clock, active');
  });

  it('locks a timed failure until canonical refresh succeeds and never retries the stale move', async () => {
    const after = { ...timedSession, clock: { ...timedClock, version: 6 } };
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
    expect(ApiClient.getGame).toHaveBeenCalledTimes(2);
    expect(queueMove).not.toHaveBeenCalled();
  });

  it('renders the server flag result, including a draw, rather than deriving a client winner', async () => {
    const ended = { ...timedSession, status: 'completed', clock: {
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

  it('resumes the explicitly linked unfinished session instead of a different stored session', async () => {
    window.history.replaceState(null, '', '/play?session=blocked-session');
    localStorage.setItem('scan64_player_id', 'test-player');
    localStorage.setItem('scan64_active_play_session_id', 'different-session');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'blocked-session', player_id: 'test-player', game_id: 'blocked-game',
      opponent_config: {}, status: 'active', coach_mode: false,
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
    const active: PlaySessionRead = { id: 'stored-a', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false };
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

  it('allows a new game and reload after consuming an unavailable query', async () => {
    window.history.replaceState(null, '', '/play?session=missing&mode=study');
    localStorage.setItem('scan64_player_id', 'test-player');
    vi.mocked(ApiClient.getPlaySession).mockRejectedValueOnce(new ApiRequestError('Missing', 404));
    const active: PlaySessionRead = { id: 'new-game', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false };
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
    const b: PlaySessionRead = { id: 'explicit-b', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false };
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
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false };
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
      opponent_config: {}, status: 'active', coach_mode: false };
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
      opponent_config: {}, status: 'active', coach_mode: false };
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
        opponent_config: {}, status: 'active', coach_mode: false }} />);
    await act(async () => { await getRegisteredMoveHandler()('g6', destination); });
    expect(await screen.findByTestId('game-result')).toHaveTextContent(result);
    expect(screen.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', '/games/game-123/analysis');
  });

  it('sends the selected under-promotion to the server', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({ opponent_move: 'a1b1', status: 'active' });
    render(<PlayScreen initialFen="8/P7/8/8/8/8/8/k6K w - - 0 1"
      initialSession={{ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
        opponent_config: {}, status: 'active', coach_mode: false }} />);
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
        opponent_config: {}, status: 'active', coach_mode: false }} />);
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
      sessionRequest.resolve({ id: 'sess-123', player_id: 'test-player', opponent_config: {}, status: 'active', coach_mode: false });
    });

    await waitFor(() => {
      expect(screen.getByTestId('session-info')).toHaveTextContent('Status: active');
    });
  });

  it('restores an active session at its persisted game position', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active', coach_mode: false });
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

  it('locks moves and resign through queued acknowledgment, failed refresh and canonical retry', async () => {
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false };
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
    expect(queueMove).toHaveBeenCalledWith('sess-123', 'e2e4');
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
    const playSession: PlaySessionRead = { id: 'sess-123', player_id: 'test-player',
      game_id: 'game-123', opponent_config: {}, status: 'active', coach_mode: false };
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
    opponent_config: {}, status: 'active', coach_mode: false });
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
    opponent_config: {}, status: 'active', coach_mode: false });
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
  it('retries a failed canonical refresh after queued-move synchronization', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(getQueuedMoves)
      .mockResolvedValueOnce([{ sessionId: 'sess-123', move: 'e2e4', timestamp: 1 }])
      .mockResolvedValueOnce([]);
    const resumedSession = { id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
    opponent_config: {}, status: 'active' as const, coach_mode: false };
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

  it('clears a terminal saved session without loading a game', async () => {
    localStorage.setItem('scan64_active_play_session_id', 'sess-123');
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({ id: 'sess-123', player_id: 'test-player', opponent_config: {}, status: 'completed', coach_mode: false });

    render(<PlayScreen />);

    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledWith('sess-123');
      expect(localStorage.getItem('scan64_active_play_session_id')).toBeNull();
    });
    expect(ApiClient.getGame).not.toHaveBeenCalled();
    expect(screen.queryByTestId('session-info')).not.toBeInTheDocument();
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
    opponent_config: {}, status: 'active', coach_mode: false });
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
        opponent_config: {}, status: 'active', coach_mode: false }}
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
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValueOnce(new Error('offline'));
    vi.mocked(queueMove).mockResolvedValue();
    const network = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'sess-123', player_id: 'test-player', game_id: 'game-123',
      opponent_config: {}, status: 'active', coach_mode: true,
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
        }}
      />,
    );
    await act(async () => {
      await getRegisteredMoveHandler()('e2', 'e4');
    });
    await waitFor(() => {
      expect(queueMove).toHaveBeenCalledWith('sess-123', 'e2e4');
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
        opponent_config: {}, status: 'active', coach_mode: false }}
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
        opponent_config: {}, status: 'active', coach_mode: false }}
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
        opponent_config: {}, status: 'active', coach_mode: false }}
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
        opponent_config: {}, status: 'active', coach_mode: false }}
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
      status: 'active', coach_mode: false });
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
      opponent_config: {}, player_color: 'black', opponent_move: 'd2d4', status: 'active', coach_mode: false });
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
      opponent_config: { player_color: 'black' }, status: 'active', coach_mode: false };
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
      player_color: 'black', status: 'active', coach_mode: false }} />);
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black',
      movable: expect.objectContaining({ color: 'black', dests: undefined }), premovable: { enabled: true } }));
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Resign' })).toBeEnabled();
  });

  it('takes back a pair and reloads the authoritative game', async () => {
    const session: PlaySessionRead = { id: 'undo', player_id: 'test-player', game_id: 'undo-game',
      player_color: 'white', opponent_config: {}, status: 'active', coach_mode: false };
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
      opponent_config: {}, status: 'active', coach_mode: false };
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
      status: 'active', coach_mode: true }} />);
    expect(screen.queryByTestId('takeback-btn')).not.toBeInTheDocument();
  });

  it('disables takeback after a live offline move is queued', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({ opponent_move: 'e7e5', status: 'active' });
    render(<PlayScreen initialSession={{ id: 'live-offline', player_id: 'test-player',
      opponent_config: {}, status: 'active', coach_mode: false }} />);
    await act(async () => { await getRegisteredMoveHandler()('e2', 'e4'); });
    await waitFor(() => expect(screen.getByTestId('takeback-btn')).toBeEnabled());
    const offline = vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValueOnce(new Error('offline'));
    vi.mocked(queueMove).mockResolvedValue();
    await act(async () => { await getRegisteredMoveHandler()('g1', 'f3'); });
    await waitFor(() => expect(queueMove).toHaveBeenCalledWith('live-offline', 'g1f3'));
    expect(screen.getByTestId('takeback-btn')).toBeDisabled();
    fireEvent.click(screen.getByTestId('takeback-btn'));
    expect(ApiClient.takebackPlaySession).not.toHaveBeenCalled();
    offline.mockRestore();
  });

});
