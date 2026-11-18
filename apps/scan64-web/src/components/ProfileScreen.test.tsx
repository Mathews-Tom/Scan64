import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ApiClient } from '../api/client';
import type { PatternsReport } from '../api/types';
import type * as ClientModule from '../api/client';
import { ProfileScreen } from './ProfileScreen';
import { onboardingFixture, strengthFixture } from './strength.test-fixtures';
import { insightsFixture } from './profile.test-fixtures';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return {
    ...actual,
    ApiClient: {
      ...actual.ApiClient,
      getPlayerProgress: vi.fn(),
      getPlayerOnboarding: vi.fn(),
      getPlayerStrength: vi.fn(),
      updatePlayerOnboarding: vi.fn(),
      getPlayerEvidence: vi.fn(),
      getPlayerPatterns: vi.fn(),
      getPlayerInsights: vi.fn(),
      getPlayerHabit: vi.fn().mockResolvedValue({
        utc_day: '2026-11-13', goal: 3, qualifying_solves: 0, remaining: 3,
        pending_goal: null, pending_effective_day: null, current_streak: 0, best_streak: 0,
        reset_at: '2026-11-14T00:00:00Z', day_status: 'in_progress', days: [], targets: [],
        supply: { remaining_daily_slots: 0, eligible_lesson_retries: 0, extra_modes_available: false },
        evidence_limits: 'Server-unhinted puzzles; app-reported lesson hints.',
      }),
      getPuzzleModes: vi.fn().mockResolvedValue({ themes: [], streak: { current: 0, best: 0, reset_reason: null } }),
    },
    getActivePlayerId: () => 'profile-player',
  };
});

function renderProfile(patterns: PatternsReport): void {
  vi.mocked(ApiClient.getPlayerOnboarding).mockResolvedValue(onboardingFixture);
  vi.mocked(ApiClient.getPlayerStrength).mockResolvedValue(strengthFixture);
  vi.mocked(ApiClient.getPlayerInsights).mockResolvedValue(insightsFixture);
  vi.spyOn(ApiClient, 'getPlayerProgress').mockResolvedValue({
    player_id: 'profile-player',
    skills: [{ concept: 'board_awareness.hanging_piece', name: 'Hanging piece', mastery: 0.25, uncertainty: 0.5 }],
  });
  vi.spyOn(ApiClient, 'getPlayerEvidence').mockResolvedValue({
    player_id: 'profile-player',
    evidence_items: [],
  });
  vi.spyOn(ApiClient, 'getPlayerPatterns').mockResolvedValue(patterns);
  render(<ProfileScreen />);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('ProfileScreen recurring diagnoses', () => {
  it('labels pooled accuracy, unavailable data and clock coverage', async () => {
    renderProfile({
      player_id: 'profile-player', minimum_occurrences: 3, eligible_game_count: 0,
      status: 'insufficient_data', recurring_diagnoses: [],
    });
    expect(await screen.findByText(/Overall: 86.0%/)).toHaveTextContent('5 / 8 scored / eligible owner moves');
    expect(screen.getByText(/Caro-Kann Defense: Unavailable/)).toHaveTextContent('0 / 1 scored / eligible owner moves');
    expect(screen.getByText(/Middlegame: Unavailable/)).toHaveTextContent('0 / 0 scored / eligible owner moves');
    expect(screen.getByText(/50.0% · 2/)).toHaveTextContent('≤30s recorded post-move clock');
    expect(screen.getByText(/real-time TimeControl/)).toHaveTextContent('clock coverage: 4 / 7');
  });

  it('preserves existing reports and retries when game insights fail', async () => {
    vi.mocked(ApiClient.getPlayerInsights).mockRejectedValueOnce(new Error('Insights request failed'));
    renderProfile({
      player_id: 'profile-player', minimum_occurrences: 3, eligible_game_count: 3,
      status: 'no_recurring_diagnosis', recurring_diagnoses: [],
    });
    const retry = await screen.findByRole('button', { name: 'Retry game insights' });
    expect(screen.getByText('Game insights unavailable: Insights request failed')).toBeInTheDocument();
    expect(await screen.findByText('Declared level: Developing.')).toBeInTheDocument();
    expect(screen.getByText('Hanging piece: 0.25 (Uncertainty: 0.50)')).toBeInTheDocument();
    expect(screen.getByText('No trusted diagnosis recurs across 3 distinct games.')).toBeInTheDocument();
    fireEvent.click(retry);
    expect(await screen.findByText(/Overall: 86.0%/)).toHaveTextContent('5 / 8 scored / eligible owner moves');
    expect(screen.queryByRole('button', { name: 'Retry game insights' })).not.toBeInTheDocument();
  });

  it('refreshes the separate strength report after changing the declaration', async () => {
    renderProfile({
      player_id: 'profile-player', minimum_occurrences: 3, eligible_game_count: 0, status: 'insufficient_data', recurring_diagnoses: [],
    });
    expect(await screen.findByText('Declared level: Developing.')).toBeInTheDocument();
    expect(screen.getByText(/Scan64 puzzle rating: 1216 \(provisional\)/)).toBeInTheDocument();
    expect(screen.getByText(/provisional \(3, 2\) seed/)).toBeInTheDocument();
    vi.mocked(ApiClient.getPlayerStrength).mockResolvedValue({ ...strengthFixture, declared_level: 'beginner', suggested_bot_level: 2 });
    vi.mocked(ApiClient.updatePlayerOnboarding).mockResolvedValue({
      ...onboardingFixture, status: 'in_progress', declared_level: 'beginner', suggested_bot_level: 2,
      intro_started_at: '2026-11-13T10:00:00Z', mapping_version: 'declared-level-v1',
    });
    fireEvent.click(await screen.findByRole('radio', { name: 'Beginner' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save level and show suggestion' }));
    expect(await screen.findByText('Declared level: Beginner.')).toBeInTheDocument();
    expect(ApiClient.getPlayerStrength).toHaveBeenLastCalledWith('profile-player');
    expect(screen.queryByText('Rating 1500')).not.toBeInTheDocument();
  });

  it('explains when the corpus is too sparse', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3, eligible_game_count: 3,
      status: 'insufficient_data',
      recurring_diagnoses: [],
    });

    await waitFor(() => {
      expect(
        screen.getByText('More eligible analysed games are needed before recurrence can be assessed.'),
      ).toBeInTheDocument();
    });
  });

  it('distinguishes no recurrence from a sparse corpus', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3, eligible_game_count: 3,
      status: 'no_recurring_diagnosis',
      recurring_diagnoses: [],
    });

    await waitFor(() => {
      expect(
        screen.getByText('No trusted diagnosis recurs across 3 distinct games.'),
      ).toBeInTheDocument();
    });
  });

  it('renders recurring diagnosis counts', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3, eligible_game_count: 3,
      status: 'recurring_diagnosis',
      recurring_diagnoses: [
        {
          diagnosis: 'tactics.fork.knight',
          name: 'Knight fork',
          occurrence_count: 3,
          game_ids: ['game-1', 'game-2', 'game-3'],
          evidence_references: ['evidence-1'], template: 'A knight forks two pieces.', total_occurrences: 4, scored_occurrences: 3, observed_loss: 0.6,
        },
      ],
    });

    await waitFor(() => {
      expect(screen.getByText(/Support: 3 distinct games/)).toBeInTheDocument();
      expect(screen.getByText('Hanging piece: 0.25 (Uncertainty: 0.50)')).toBeInTheDocument();
      expect(screen.queryByText(/board_awareness|tactics\.fork/)).not.toBeInTheDocument();
    });
  });
});
