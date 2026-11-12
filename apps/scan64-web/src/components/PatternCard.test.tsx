import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { PatternsReport } from '../api/types';
import { PatternCard } from './PatternCard';

const report: PatternsReport = {
  player_id: 'owner', eligible_game_count: 5, minimum_occurrences: 3, status: 'recurring_diagnosis',
  recurring_diagnoses: [{ diagnosis: 'fork', name: 'Fork', template: 'Two targets attacked.', occurrence_count: 3,
    game_ids: ['one', 'two', 'three'], evidence_references: [], observed_loss: 0.75,
    scored_occurrences: 3, total_occurrences: 4, practice_themes: ['fork'],
    supporting_moves: [{ game_id: 'one', ply: 7, lesson_id: 'lesson', loss: 0.25 }] }],
};
const excluded = { no_review_schedule: 0, retired: 0, untrusted: 0, exhausted: 0 };
afterEach(() => vi.restoreAllMocks());

describe('PatternCard', () => {
  it('shows cost, distinct support, coverage, template and exact owner position', () => {
    render(<PatternCard report={report} />);
    expect(screen.getByText(/75.0 percentage points/)).toBeInTheDocument();
    expect(screen.getByText(/Support: 3 distinct games · Coverage: 3\/4/)).toBeInTheDocument();
    expect(screen.getByText('Two targets attacked.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Game one, ply 7' })).toHaveAttribute('href', '/games/one/analysis?ply=7');
    expect(screen.getByText(/not all errors/)).toBeInTheDocument();
  });
  it('withholds every review and practice action from coaches', () => {
    render(<PatternCard report={report} owner={false} />);
    expect(screen.getByText(/75.0 percentage points/)).toBeInTheDocument();
    expect(screen.queryByRole('link')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
  it('does not call an unscored pattern costliest or substitute zero', () => {
    render(<PatternCard report={{ ...report, recurring_diagnoses: [{ ...report.recurring_diagnoses[0], observed_loss: null, scored_occurrences: 0 }] }} />);
    expect(screen.getByText(/Observed loss unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/Highest observed loss/)).not.toBeInTheDocument();
  });
  it('searches subsequent retry pages before choosing theme practice', async () => {
    const navigate = vi.fn();
    vi.spyOn(ApiClient, 'getPuzzleMistakes').mockResolvedValueOnce({ items: [], excluded, offset: 0, limit: 20, has_more: true })
      .mockResolvedValueOnce({ items: [{ lesson_id: 'other-same-code-lesson', game_id: 'one', ply: 7, attempts_remaining: 2, diagnosis: report.recurring_diagnoses[0].diagnosis }], excluded, offset: 20, limit: 20, has_more: false });
    const modes = vi.spyOn(ApiClient, 'getPuzzleModes');
    render(<PatternCard report={report} onNavigate={navigate} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start pattern practice' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/puzzles?lesson=other-same-code-lesson&game=one&fallbackTheme=fork'));
    expect(ApiClient.getPuzzleMistakes).toHaveBeenNthCalledWith(2, 20);
    expect(modes).not.toHaveBeenCalled();
  });
  it('routes mapped practice into an unrated theme without starting Daily', async () => {
    const navigate = vi.fn();
    vi.spyOn(ApiClient, 'getPuzzleMistakes').mockResolvedValue({ items: [], excluded, offset: 0, limit: 20, has_more: false });
    vi.spyOn(ApiClient, 'getPuzzleModes').mockResolvedValue({ themes: ['fork'], streak: { current: 0, best: 0, reset_reason: null } });
    render(<PatternCard report={report} onNavigate={navigate} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start pattern practice' }));
    await waitFor(() => expect(navigate).toHaveBeenCalledWith('/puzzles?theme=fork'));
  });
  it('offers only a plainly unrelated Daily link when related supply is absent', async () => {
    vi.spyOn(ApiClient, 'getPuzzleMistakes').mockResolvedValue({ items: [], excluded, offset: 0, limit: 20, has_more: false });
    vi.spyOn(ApiClient, 'getPuzzleModes').mockResolvedValue({ themes: [], streak: { current: 0, best: 0, reset_reason: null } });
    const start = vi.spyOn(ApiClient, 'startPuzzleRun');
    render(<PatternCard report={report} />);
    fireEvent.click(screen.getByRole('button', { name: 'Start pattern practice' }));
    expect(await screen.findByRole('link', { name: 'Daily puzzles — may be unrelated' })).toHaveAttribute('href', '/puzzles');
    expect(start).not.toHaveBeenCalled();
  });
});
