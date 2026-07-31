import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PlayScreen } from './PlayScreen';
import { ApiClient, ApiRequestError, setActivePlayerId } from '../api/client';
import type { LessonSpec, PlaySessionRead } from '../api/types';
import {
  getQueuedMoves,
  QUEUED_MOVE_SYNC_SUCCEEDED,
  queueMove,
  syncQueuedMoves,
} from '../api/offlineQueue';

type MoveHandler = (orig: string, dest: string) => void | Promise<void>;

const chessgroundMock = vi.hoisted(() => ({
  after: undefined as MoveHandler | undefined,
  color: undefined as 'white' | 'black' | undefined,
  set: vi.fn(),
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
    }
  ) => {
    chessgroundMock.after = config.movable.events?.after;
    chessgroundMock.color = config.movable.color;
    return { set: chessgroundMock.set, redrawAll: chessgroundMock.redrawAll, destroy: chessgroundMock.destroy };
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
  schema_version: '0.1.0',
  lesson_id: 'lesson-123',
  source: { kind: 'pgn', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
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
    createPlayer: vi.fn(),
    createPlaySession: vi.fn(),
    makePlaySessionMove: vi.fn(),
    getGame: vi.fn(),
    getPlaySession: vi.fn(),
    getTrainingSession: vi.fn().mockResolvedValue({ session_id: 'study-1', lessons: [] }),
    recordLessonAttempt: vi.fn(),
  },
  setActivePlayerId: vi.fn(),
}));

describe('PlayScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    chessgroundMock.after = undefined;
    chessgroundMock.color = undefined;
    chessgroundMock.set.mockReset();
    chessgroundMock.redrawAll.mockReset();
    chessgroundMock.destroy.mockReset();
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({ session_id: 'study-1', lessons: [] });
    vi.mocked(getQueuedMoves).mockResolvedValue([]);
  });

  it('renders start button and board container', () => {
    render(<PlayScreen />);
    expect(screen.getByTestId('start-btn')).toBeTruthy();
    expect(screen.getByTestId('chessground-board')).toBeTruthy();
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
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(ApiClient.createPlayer).toHaveBeenCalledWith({ id: 'test-player', display_name: 'Anonymous' });
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith({
        player_id: 'test-player',
        opponent_config: { strength: '1500' },
        coach_mode: false,
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
    });
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
    expect(setActivePlayerId).toHaveBeenCalledWith('test-player');
    expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(1);
    expect(ApiClient.getGame).toHaveBeenCalledTimes(1);
    expect(screen.queryByText(/queued move|synchronizing game/i)).not.toBeInTheDocument();
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
    expect(chessgroundMock.set).toHaveBeenCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );


    await waitFor(() => {
      expect(ApiClient.getPlaySession).toHaveBeenCalledTimes(2);
      expect(ApiClient.getGame).toHaveBeenCalledTimes(2);
      expect(chessgroundMock.set).toHaveBeenLastCalledWith(
        expect.objectContaining({
          fen: 'r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3',
          movable: expect.objectContaining({ color: 'white', dests: expect.any(Map) }),
        }),
      );
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
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: undefined, dests: undefined }),
      }),
    );
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
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);

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
    fireEvent.click(screen.getByTestId('complete-critical-review'));
    expect(chessgroundMock.set).toHaveBeenLastCalledWith(
      expect.objectContaining({
        movable: expect.objectContaining({ color: 'white', dests: expect.any(Map) }),
      }),
    );
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
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    const move = getRegisteredMoveHandler();
    await act(async () => {
      await move('e2', 'e4');
    });

    expect(ApiClient.createPlaySession).toHaveBeenCalledWith(
      expect.objectContaining({
        opponent_config: { strength: '1500' },
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
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(screen.getByText('Network Error')).toBeTruthy();
    });
  });
});
