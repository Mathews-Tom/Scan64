import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient } from '../api/client';

vi.mock('../api/client', () => ({
  ApiClient: {
    getPositions: vi.fn(),
  },
}));

describe('AnalysisScreen', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a chess board and MultiPV analysis when provided a gameId', async () => {
    const mockPositions = [
      {
        id: 'pos-1',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        half_move_clock: 0,
        full_move_number: 1,
        side_to_move: 'w',
        canonical_id: 'start',
        analysis: {
          id: 'analysis-1',
          config: {},
          raw_result: [
            { pv: ['e2e4', 'e7e5'], score_cp: 35 },
            { pv: ['d2d4', 'd7d5'], score_cp: 25 },
          ]
        }
      }
    ];

    vi.mocked(ApiClient.getPositions).mockResolvedValue(mockPositions as any);

    render(<AnalysisScreen gameId="game-1" />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    expect(ApiClient.getPositions).toHaveBeenCalledWith('game-1');

    const multiPvContainer = screen.getByTestId('multipv-lines');
    expect(multiPvContainer).toHaveTextContent('0.35 - e2e4 e7e5');
    expect(multiPvContainer).toHaveTextContent('0.25 - d2d4 d7d5');
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
});
