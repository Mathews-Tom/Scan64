import { act, render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { AnalysisScreen } from './AnalysisScreen';
import { ApiClient, ApiRequestError } from '../api/client';
import type { PositionRead } from '../api/types';

vi.mock('../api/client', () => ({
  ApiClient: {
    getPositions: vi.fn(),
    getGameAnalysisStatus: vi.fn(),
    createGame: vi.fn(),
    createAnalysisJob: vi.fn(),
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
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValue({ status: 'completed' });
  });

  it('renders a chess board and MultiPV analysis when provided a gameId', async () => {
    const mockPositions: PositionRead[] = [
      {
        id: 'pos-1',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        half_move_clock: 0,
        full_move_number: 1,
        side_to_move: 'w',
        canonical_id: 'start',
        diagnoses: [
          {
            primary: 'tactics.hanging_piece',
            secondary: [],
            confidence: 0.9,
          },
        ],
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

    vi.mocked(ApiClient.getPositions).mockResolvedValue(mockPositions);

    render(<AnalysisScreen gameId="game-1" />);

    expect(screen.getByText('Loading...')).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.queryByText('Loading...')).not.toBeInTheDocument();
    });

    expect(ApiClient.getPositions).toHaveBeenCalledWith('game-1');

    const multiPvContainer = screen.getByTestId('multipv-lines');
    expect(multiPvContainer).toHaveTextContent('0.35 - e2e4 e7e5');
    expect(multiPvContainer).toHaveTextContent('0.25 - d2d4 d7d5');
    expect(screen.getByTestId('diagnosis-marker')).toHaveTextContent('tactics.hanging_piece');
    expect(screen.getByTestId('position-diagnoses')).toHaveTextContent(
      'tactics.hanging_piece (90%)',
    );
  });

  it('reports a missing or unowned game analysis', async () => {
    vi.mocked(ApiClient.getPositions).mockRejectedValue(
      new ApiRequestError('Failed to get positions: Not Found', 404),
    );

    render(<AnalysisScreen gameId="missing-game" />);

    expect(await screen.findByText('Game analysis was not found.')).toBeInTheDocument();
  });

  it('distinguishes a game that has not been analysed yet', async () => {
    vi.mocked(ApiClient.getPositions).mockResolvedValue([]);
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValue({ status: 'not_analysed' });

    render(<AnalysisScreen gameId="unanalyzed-game" />);

    expect(await screen.findByTestId('analysis-not-analysed')).toHaveTextContent(
      'This game has not been analysed yet.',
    );
    expect(screen.queryByTestId('analysis-found-nothing')).not.toBeInTheDocument();
  });

  it('starts analysis for an owned game that has not been analysed', async () => {
    vi.mocked(ApiClient.getPositions).mockResolvedValue([]);
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValue({ status: 'not_analysed' });
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValue({
      id: 'job-1',
      game_id: 'unanalyzed-game',
      status: 'pending',
    });

    render(<AnalysisScreen gameId="unanalyzed-game" />);

    fireEvent.click(await screen.findByTestId('start-analysis'));

    await waitFor(() => {
      expect(ApiClient.createAnalysisJob).toHaveBeenCalledWith('unanalyzed-game');
    });
    expect(screen.getByTestId('analysis-in-progress')).toHaveTextContent('Analysis is in progress.');
  });

  it('reports a failed analysis without presenting it as in progress', async () => {
    vi.mocked(ApiClient.getPositions).mockResolvedValue([]);
    vi.mocked(ApiClient.getGameAnalysisStatus).mockResolvedValue({ status: 'failed' });

    render(<AnalysisScreen gameId="failed-game" />);

    expect(await screen.findByTestId('analysis-failed')).toHaveTextContent('Analysis failed.');
    expect(screen.getByTestId('retry-analysis')).toBeInTheDocument();
    expect(screen.queryByTestId('analysis-in-progress')).not.toBeInTheDocument();
  });

  it('distinguishes completed analysis with no diagnoses', async () => {
    vi.mocked(ApiClient.getPositions).mockResolvedValue([]);

    render(<AnalysisScreen gameId="quiet-game" />);

    expect(await screen.findByTestId('analysis-found-nothing')).toHaveTextContent(
      'Analysis found no diagnoses.',
    );
    expect(screen.queryByTestId('analysis-not-analysed')).not.toBeInTheDocument();
  });

  it('ignores a stale missing-game response after the addressed game changes', async () => {
    let rejectFirst!: (reason?: unknown) => void;
    const firstRequest = new Promise<never>((_, reject) => {
      rejectFirst = reject;
    });
    vi.mocked(ApiClient.getPositions).mockReturnValueOnce(firstRequest).mockResolvedValueOnce([]);

    const { rerender } = render(<AnalysisScreen gameId="missing-game" />);
    rerender(<AnalysisScreen gameId="empty-game" />);

    await waitFor(() => expect(ApiClient.getPositions).toHaveBeenLastCalledWith('empty-game'));
    await act(async () => {
      rejectFirst(new ApiRequestError('Failed to get positions: Not Found', 404));
      await Promise.resolve();
    });

    expect(screen.queryByText('Game analysis was not found.')).not.toBeInTheDocument();
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
    opponent_config: { strength: '1500' }, status: 'active', coach_mode: false };
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
        expect.objectContaining({ game_id: 'game-1' })
      );
    });
    expect(onPlayFromHere).toHaveBeenCalledWith(
      playSession,
      'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
    );
  });
});
