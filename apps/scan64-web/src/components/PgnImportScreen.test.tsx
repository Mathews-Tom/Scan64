import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PgnImportScreen } from './PgnImportScreen';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';

vi.mock('../api/client', () => ({
  ApiClient: {
    createGame: vi.fn(),
    createPlayer: vi.fn(),
    getLearningOpportunities: vi.fn(),
    createAnalysisJob: vi.fn(),
    getAnalysisJob: vi.fn(),
  },
  ensurePlayerAuthorization: vi.fn(),
  getOrCreatePlayerId: vi.fn(() => 'player-1'),
}));

describe('PgnImportScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getOrCreatePlayerId).mockReturnValue('player-1');
    vi.mocked(ensurePlayerAuthorization).mockResolvedValue('recovered-player-2');
  });

  it('renders textarea and button', () => {
    render(<PgnImportScreen />);
    expect(screen.getByTestId('pgn-textarea')).toBeTruthy();
    expect(screen.getByTestId('import-btn')).toBeTruthy();
  });

  it('imports PGN and displays learning opportunities', async () => {
    const onExploreAnalysis = vi.fn();
    vi.mocked(ApiClient.createGame).mockResolvedValueOnce({
      id: 'game-1', pgn: '...', white: 'w', black: 'b', result: '*'
    });
    vi.mocked(ApiClient.createAnalysisJob).mockResolvedValueOnce({
      id: 'job-1', game_id: 'game-1', status: 'pending'
    });
    vi.mocked(ApiClient.getAnalysisJob).mockResolvedValueOnce({
      id: 'job-1', game_id: 'game-1', status: 'completed'
    });
    vi.mocked(ApiClient.getLearningOpportunities).mockResolvedValueOnce({
      session_id: 'study-1',
      lessons: [
        {
          schema_version: '1', lesson_id: 'les-1',
          source: { kind: 'pgn', fen: 'fen' },
          diagnosis: { primary: 'tactics.fork', secondary: [], confidence: 0.9, evidence_refs: [] },
          objective: { type: 'play', instruction: 'win' },
          interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
          hints: [], explanation: { text: 'exp' },
          verification: { status: 'verified', engine: 'e', engine_binary_digest: 'd', nodes: 1, multipv: 1, verified_at: 'now' },
          mastery: { skill_key: 'key', delta: 0.1 },
        },
      ],
      next_cursor: null,
    });

    render(<PgnImportScreen onExploreAnalysis={onExploreAnalysis} />);
    const textarea = screen.getByTestId('pgn-textarea');
    fireEvent.change(textarea, { target: { value: '1. e4 e5' } });
    
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => {
      expect(ensurePlayerAuthorization).toHaveBeenCalledWith('player-1');
      expect(ApiClient.createGame).toHaveBeenCalledWith({ pgn: '1. e4 e5', player_id: 'recovered-player-2' });
      expect(ApiClient.createAnalysisJob).toHaveBeenCalledWith('game-1');
      expect(ApiClient.getAnalysisJob).toHaveBeenCalledWith('job-1');
      expect(ApiClient.getLearningOpportunities).toHaveBeenCalledWith('game-1');
      expect(screen.getByTestId('lessons-list').textContent).toContain('tactics.fork');
    }, { timeout: 3000 });

    fireEvent.click(screen.getByTestId('explore-analysis-btn'));
    expect(onExploreAnalysis).toHaveBeenCalledWith('game-1');
  });
});
