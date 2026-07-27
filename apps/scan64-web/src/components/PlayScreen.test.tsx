import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { PlayScreen } from './PlayScreen';
import { ApiClient } from '../api/client';
import type { LessonSpec } from '../api/types';

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

// Mock the API client
vi.mock('../api/client', () => ({
  ApiClient: {
    createPlayer: vi.fn(),
    createPlaySession: vi.fn(),
    makePlaySessionMove: vi.fn(),
    getTrainingSession: vi.fn().mockResolvedValue({ session_id: 'study-1', lessons: [] }),
    recordLessonAttempt: vi.fn(),
  },
  setActivePlayerId: vi.fn(),
}));

describe('PlayScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    chessgroundMock.after = undefined;
    chessgroundMock.color = undefined;
    chessgroundMock.set.mockReset();
    chessgroundMock.redrawAll.mockReset();
    chessgroundMock.destroy.mockReset();
    vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({ session_id: 'study-1', lessons: [] });
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
      opponent_config: {},
    });

    render(<PlayScreen />);
    fireEvent.change(screen.getByTestId('player-id-input'), { target: { value: 'test-player' } });
    fireEvent.click(screen.getByTestId('start-btn'));

    await waitFor(() => {
      expect(ApiClient.createPlayer).toHaveBeenCalledWith({ id: 'test-player', display_name: 'Anonymous' });
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith({ player_id: 'test-player', opponent_config: { strength: '1500' } });
      expect(screen.getByTestId('session-info').textContent).toContain('Status: active');
      expect(chessgroundMock.set).toHaveBeenCalledWith(
        expect.objectContaining({
          movable: expect.objectContaining({ color: 'white' }),
        })
      );
      // The setup form unmounts when the session starts, moving the board; without
      // this the cached Chessground bounds leave the board unresponsive to pointers.
      expect(chessgroundMock.redrawAll).toHaveBeenCalled();
    });
  });

  it('submits a board move from an initial play session', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: null,
      status: 'active',
    });

    render(
      <PlayScreen
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
        }}
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

  it('marks the session completed after an opponent checkmate', async () => {
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValueOnce({
      opponent_move: 'd8h4',
      status: 'completed',
    });

    render(
      <PlayScreen
        initialFen="rnbqkbnr/pppp1ppp/8/4p3/8/5P2/PPPPP1PP/RNBQKBNR w KQkq - 0 2"
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
        }}
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
  });

  it('restores Chessground to the player turn after an opponent response', async () => {
    vi.mocked(ApiClient.makePlaySessionMove)
      .mockResolvedValueOnce({ opponent_move: 'e7e5', status: 'active' })
      .mockResolvedValueOnce({ opponent_move: 'b8c6', status: 'active' });

    render(
      <PlayScreen
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
        }}
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
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
        }}
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

  it('shows an interruption review only when coach mode is enabled', async () => {
    const interruptionLesson: LessonSpec = {
      schema_version: '0.1.0',
      lesson_id: 'lesson-1',
      source: { kind: 'pgn', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
      diagnosis: { primary: 'tactic', secondary: [], confidence: 1, evidence_refs: [] },
      objective: { type: 'move', instruction: 'Find the tactic' },
      interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
      hints: [{ level: 1, kind: 'cue', text: 'Look again' }],
      explanation: { text: 'The tactic wins material' },
      verification: {
        status: 'verified',
        engine: 'test',
        engine_binary_digest: 'digest',
        nodes: 1,
        multipv: 1,
        verified_at: 'now',
      },
      mastery: { skill_key: 'tactics.fork', delta: 0.1 },
    };
    vi.mocked(ApiClient.createPlayer).mockResolvedValue({
      id: 'test-player',
      preferences: {},
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue({
      id: 'sess-123',
      player_id: 'test-player',
      status: 'active',
      opponent_config: {},
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: null,
      status: 'active',
      interruption_lesson: interruptionLesson,
    });

    const firstScreen = render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('start-btn'));
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    const disabledCoachMove = getRegisteredMoveHandler();
    await act(async () => {
      await disabledCoachMove('e2', 'e4');
    });
    expect(screen.queryByTestId('critical-moment-review')).toBeNull();

    firstScreen.unmount();
    chessgroundMock.after = undefined;
    render(<PlayScreen />);
    fireEvent.click(screen.getByTestId('coach-mode-toggle'));
    fireEvent.click(screen.getByTestId('start-btn'));
    await waitFor(() => expect(chessgroundMock.after).toBeDefined());
    const enabledCoachMove = getRegisteredMoveHandler();
    await act(async () => {
      await enabledCoachMove('e2', 'e4');
    });
    expect(screen.getByTestId('critical-moment-review')).toBeInTheDocument();
  });

  it('allows a black-to-move initial position', async () => {
    render(
      <PlayScreen
        initialFen="rnbqkbnr/pppppppp/8/8/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
        initialSession={{
          id: 'sess-123',
          player_id: 'test-player',
          game_id: 'game-123',
          opponent_config: {},
          status: 'active',
        }}
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
