import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { PatternsReport } from '../api/types';
import { TodayScreen } from './TodayScreen';
import { ProfileScreen } from './ProfileScreen';

vi.mock('./OnboardingCard', () => ({ OnboardingCard: () => null }));
vi.mock('./StrengthSummary', () => ({ StrengthSummary: () => null }));
vi.mock('./HabitPanel', () => ({
  HabitPanel: ({ playerId, patterns, onNavigate }: {
    playerId: string; patterns: PatternsReport; onNavigate?: (path: string) => void;
  }) => <section aria-label="Shared habit panel">
    <p>Habit owner: {playerId}; Pattern: {patterns.recurring_diagnoses[0]?.name}</p>
    <button onClick={() => onNavigate?.('/training')}>Open habit practice</button>
  </section>,
}));
vi.mock('../api/client', () => {
  const patterns: PatternsReport = {
    player_id: 'habit-owner', minimum_occurrences: 3, eligible_game_count: 5,
    status: 'recurring_diagnosis', recurring_diagnoses: [{
      diagnosis: 'board_awareness.hanging_piece', name: 'Hanging piece', template: 'A piece is undefended.',
      occurrence_count: 3, game_ids: [], evidence_references: [], total_occurrences: 4, scored_occurrences: 3, observed_loss: 0.6,
    }],
  };
  return {
    getActivePlayerId: () => 'habit-owner',
    ensurePlayerAuthorization: async (playerId: string) => playerId,
    ApiClient: {
      getPlayerGames: vi.fn().mockResolvedValue({ items: [] }),
      getWeeklyReport: vi.fn().mockResolvedValue({ games_played: 0, active_mastery: [], active_concepts_observed: 0 }),
      getPlayerPatterns: vi.fn().mockResolvedValue(patterns),
      getPuzzleDaily: vi.fn().mockResolvedValue({ rating: 1200, provisional: true, rated_count: 0, completed: 0, total: 0, remaining: 0, overdue_count: 0, message: 'No supply' }),
      getPlayerProgress: vi.fn().mockResolvedValue({ player_id: 'habit-owner', skills: [] }),
      getPlayerEvidence: vi.fn().mockResolvedValue({ player_id: 'habit-owner', evidence_items: [] }),
      getPlayerStrength: vi.fn().mockResolvedValue({}),
    },
  };
});

describe('Today and Profile habit integration', () => {
  it.each([
    ['Today', TodayScreen],
    ['Profile', ProfileScreen],
  ] as const)('passes the active owner, recurring pattern and navigation into the shared panel on %s', async (_name, Screen) => {
    const navigate = vi.fn();
    render(<Screen onNavigate={navigate} />);
    expect(await screen.findByRole('region', { name: 'Shared habit panel' })).toHaveTextContent('Habit owner: habit-owner; Pattern: Hanging piece');
    fireEvent.click(screen.getByRole('button', { name: 'Open habit practice' }));
    expect(navigate).toHaveBeenCalledWith('/training');
  });
});
