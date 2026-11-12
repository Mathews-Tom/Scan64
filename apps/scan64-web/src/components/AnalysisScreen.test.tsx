import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient } from '../api/client';

vi.mock('chessground', () => ({
  Chessground: () => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }),
}));

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

  it('uses the shared accessible board for UCI and SAN without losing the authoritative FEN', async () => {
    render(<AnalysisScreen />);
    expect(screen.getByText('Read board position')).toBeInTheDocument();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    const entry = screen.getByLabelText('Move (UCI or SAN)');
    fireEvent.change(entry, { target: { value: ' E2E4 ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(screen.getByLabelText('FEN')).toHaveValue(
      'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
    ));
    fireEvent.change(entry, { target: { value: 'e5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(screen.getByTestId('free-analysis-board-san-move-list')).toHaveTextContent('1... e5'));
    expect(screen.getByText('e4: White pawn')).toBeInTheDocument();
    expect(screen.getByText('e5: Black pawn')).toBeInTheDocument();
  });

  it('loads a promotion position without implicitly promoting a keyboard move', async () => {
    render(<AnalysisScreen />);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: '7k/4P3/8/8/8/8/8/K7 w - - 0 1' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'e7e8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    expect(screen.getByRole('group', { name: 'Choose promotion piece' })).toBeInTheDocument();
    expect(screen.getByLabelText('FEN')).toHaveValue('7k/4P3/8/8/8/8/8/K7 w - - 0 1');
    fireEvent.click(screen.getByRole('button', { name: 'Promote to rook' }));
    await waitFor(() => expect(screen.getByText('e8: White rook')).toBeInTheDocument());
    expect(screen.queryByText('e8: White knight')).not.toBeInTheDocument();
  });

  it('announces an invalid FEN and restores focus to its labelled field', () => {
    render(<AnalysisScreen />);
    fireEvent.change(screen.getByLabelText('FEN'), { target: { value: 'not a position' } });
    fireEvent.click(screen.getByRole('button', { name: 'Load FEN' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Invalid FEN');
    expect(screen.getByLabelText('FEN')).toHaveFocus();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
  });

  it('copies the current FEN to the clipboard', async () => {
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
    expect(await screen.findByText('FEN copied.')).toBeInTheDocument();
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
