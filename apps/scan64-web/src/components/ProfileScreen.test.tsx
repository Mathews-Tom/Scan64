import { afterEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ApiClient } from '../api/client';
import type { PatternsReport } from '../api/types';
import type * as ClientModule from '../api/client';
import { ProfileScreen } from './ProfileScreen';

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof ClientModule>();
  return {
    ...actual,
    ApiClient: {
      ...actual.ApiClient,
      getPlayerProgress: vi.fn(),
      getPlayerEvidence: vi.fn(),
      getPlayerPatterns: vi.fn(),
    },
    getOrCreatePlayerId: () => 'profile-player',
  };
});

function renderProfile(patterns: PatternsReport): void {
  vi.spyOn(ApiClient, 'getPlayerProgress').mockResolvedValue({
    player_id: 'profile-player',
    skills: [],
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
  it('explains when the corpus is too sparse', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3,
      status: 'insufficient_data',
      recurring_diagnoses: [],
    });

    await waitFor(() => {
      expect(
        screen.getByText('More analysed games are needed before recurrence can be assessed.'),
      ).toBeInTheDocument();
    });
  });

  it('distinguishes no recurrence from a sparse corpus', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3,
      status: 'no_recurring_diagnosis',
      recurring_diagnoses: [],
    });

    await waitFor(() => {
      expect(
        screen.getByText('No diagnosis has recurred across 3 games.'),
      ).toBeInTheDocument();
    });
  });

  it('renders recurring diagnosis counts', async () => {
    renderProfile({
      player_id: 'profile-player',
      minimum_occurrences: 3,
      status: 'recurring_diagnosis',
      recurring_diagnoses: [
        {
          diagnosis: 'tactics.fork.knight',
          occurrence_count: 3,
          game_ids: ['game-1', 'game-2', 'game-3'],
          evidence_references: ['evidence-1'],
        },
      ],
    });

    await waitFor(() => {
      expect(screen.getByText('tactics.fork.knight: 3 games')).toBeInTheDocument();
    });
  });
});
