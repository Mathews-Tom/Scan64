import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PgnImportScreen } from './PgnImportScreen';
import { ApiClient } from '../api/client';

vi.mock('../api/client', () => ({
  ApiClient: {
    createGame: vi.fn(),
    getLearningOpportunities: vi.fn(),
    createAnalysisJob: vi.fn(),
    getAnalysisJob: vi.fn(),
  },
}));

describe('PgnImportScreen', () => {
  beforeEach(() => {
    vi.resetAllMocks();
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
    vi.mocked(ApiClient.getLearningOpportunities).mockResolvedValueOnce([
      {
        schema_version: '1', lesson_id: 'les-1', 
        source: { kind: 'pgn', fen: 'fen' },
        diagnosis: { primary: 'tactics.fork', secondary: [], confidence: 0.9, evidence_refs: [] },
        objective: { type: 'play', instruction: 'win' },
        interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
        hints: [], explanation: { text: 'exp' }, 
        // The frontend code expects verification.status === 'verified' or no verification object
        verification: { status: 'verified', engine: 'e', engine_binary_digest: 'd', nodes: 1, multipv: 1, verified_at: 'now' },
        mastery: { skill_key: 'key', delta: 0.1 }
      }
    ]);

    render(<PgnImportScreen onExploreAnalysis={onExploreAnalysis} />);
    const textarea = screen.getByTestId('pgn-textarea');
    fireEvent.change(textarea, { target: { value: '1. e4 e5' } });
    
    fireEvent.click(screen.getByTestId('import-btn'));
    await waitFor(() => {
      expect(ApiClient.createGame).toHaveBeenCalledWith({ pgn: '1. e4 e5' });
      expect(ApiClient.createAnalysisJob).toHaveBeenCalledWith('game-1');
      expect(ApiClient.getAnalysisJob).toHaveBeenCalledWith('job-1');
      expect(ApiClient.getLearningOpportunities).toHaveBeenCalledWith('game-1');
      expect(screen.getByTestId('lessons-list').textContent).toContain('tactics.fork');
    }, { timeout: 3000 });

    fireEvent.click(screen.getByTestId('explore-analysis-btn'));
    expect(onExploreAnalysis).toHaveBeenCalledWith('game-1');
  });
});
