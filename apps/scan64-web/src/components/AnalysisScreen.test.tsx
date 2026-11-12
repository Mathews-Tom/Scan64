import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient } from '../api/client';

vi.mock('../api/client', () => ({
  ApiClient: {
    createGame: vi.fn(),
    createPlaySession: vi.fn(),
  },
  ensurePlayerAuthorization: vi.fn(() => Promise.resolve('player-1')),
  getOrCreatePlayerId: vi.fn(() => 'player-1'),
  ApiRequestError: class ApiRequestError extends Error {
    status: number;

    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

describe('AnalysisScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders gracefully without a gameId', () => {
    render(<AnalysisScreen />);
    expect(screen.getByText('Analysis Board')).toBeInTheDocument();
    expect(screen.getByText('No engine analysis available for this position.')).toBeInTheDocument();
  });
  it('allows setting arbitrary FEN positions', () => {
    render(<AnalysisScreen />);

    const fenInput = screen.getByPlaceholderText('Paste FEN here');
    const loadBtn = screen.getByText('Load FEN');
    const testFen = 'rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2';

    fireEvent.change(fenInput, { target: { value: testFen } });
    fireEvent.click(loadBtn);

    expect(fenInput).toHaveValue(testFen);
    expect(screen.queryByText('Invalid FEN')).not.toBeInTheDocument();
  });

  it('copies the current FEN to the clipboard', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });

    render(<AnalysisScreen />);
    fireEvent.click(screen.getByText('Copy FEN'));

    expect(writeText).toHaveBeenCalledWith(
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    );
  });

  it('starts a play session from the analysed position', async () => {
    const onPlayFromHere = vi.fn();
    const playSession = { id: 'session-1', player_id: 'player-1', game_id: 'game-1',
    opponent_config: { provider: 'stockfish', bot_level: '1' }, status: 'active', coach_mode: false };
    vi.mocked(ApiClient.createGame).mockResolvedValue({
      id: 'game-1',
      pgn: '',
      white: 'White',
      black: 'Black',
      result: '*',
    });
    vi.mocked(ApiClient.createPlaySession).mockResolvedValue(playSession);

    render(<AnalysisScreen onPlayFromHere={onPlayFromHere} />);
    fireEvent.click(screen.getByTestId('play-from-here'));

    await waitFor(() => {
      expect(ApiClient.createPlaySession).toHaveBeenCalledWith(
        expect.objectContaining({ game_id: 'game-1', opponent_config: { provider: 'stockfish', bot_level: '1' } })
      );
    });
    expect(onPlayFromHere).toHaveBeenCalledWith(
      playSession,
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    );
  });
});
