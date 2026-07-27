import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { CoachDashboard } from '../api/types';
import { CoachDashboardScreen } from './CoachDashboardScreen';

vi.mock('../api/client', () => ({
  ApiClient: {
    getCoachDashboard: vi.fn(),
  },
  getOrCreatePlayerId: vi.fn(() => 'coach-1'),
}));

const dashboard: CoachDashboard = {
  coach_id: 'coach-1',
  students: [
    {
      student_id: 'student-1',
      profile: {
        player_id: 'student-1',
        rating: 1530,
        display_name: 'Student One',
      },
      patterns: {
        player_id: 'student-1',
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
      },
      evidence: {
        player_id: 'student-1',
        evidence_items: [
          {
            evidence_id: 'evidence-1',
            kind: 'tactical-motif',
            position_id: 'position-1',
            claim: 'Missed a knight fork',
            payload: { motif: 'knight-fork' },
            producer: { name: 'scan64', version: '1' },
          },
        ],
      },
    },
  ],
};

describe('CoachDashboardScreen', () => {
  beforeEach(() => {
    vi.spyOn(ApiClient, 'getCoachDashboard').mockResolvedValue(dashboard);
  });

  afterEach(() => {
    vi.clearAllMocks();
  });

  it('renders linked student patterns with their evidence trail', async () => {
    render(<CoachDashboardScreen />);

    expect(screen.getByTestId('coach-dashboard-loading')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByTestId('coach-dashboard')).toBeInTheDocument();
    });

    expect(ApiClient.getCoachDashboard).toHaveBeenCalledWith('coach-1');
    expect(screen.getByText('Student One')).toBeInTheDocument();
    expect(screen.getByText('tactics.fork.knight: 3 games')).toBeInTheDocument();
    expect(screen.getByText('Missed a knight fork')).toBeInTheDocument();
    expect(screen.getByText('tactical-motif')).toBeInTheDocument();
    expect(screen.getByText('scan64 · 1')).toBeInTheDocument();
  });

  it('distinguishes no recurrence from insufficient evidence', async () => {
    vi.spyOn(ApiClient, 'getCoachDashboard').mockResolvedValue({
      ...dashboard,
      students: [
        {
          ...dashboard.students[0],
          patterns: {
            player_id: 'student-1',
            minimum_occurrences: 3,
            status: 'no_recurring_diagnosis',
            recurring_diagnoses: [],
          },
        },
      ],
    });

    render(<CoachDashboardScreen />);

    await waitFor(() => {
      expect(
        screen.getByText('No diagnosis has recurred across 3 games.'),
      ).toBeInTheDocument();
    });
  });

  it('reports when the student corpus is insufficient', async () => {
    vi.spyOn(ApiClient, 'getCoachDashboard').mockResolvedValue({
      ...dashboard,
      students: [
        {
          ...dashboard.students[0],
          patterns: {
            player_id: 'student-1',
            minimum_occurrences: 3,
            status: 'insufficient_data',
            recurring_diagnoses: [],
          },
        },
      ],
    });

    render(<CoachDashboardScreen />);

    await waitFor(() => {
      expect(
        screen.getByText('More analysed games are needed before recurrence can be assessed.'),
      ).toBeInTheDocument();
    });
  });

  it('reports a failed dashboard request', async () => {
    vi.spyOn(ApiClient, 'getCoachDashboard').mockRejectedValue(new Error('Forbidden'));

    render(<CoachDashboardScreen />);

    await waitFor(() => {
      expect(screen.getByText('Dashboard unavailable')).toBeInTheDocument();
    });
    expect(screen.getByText('Forbidden')).toBeInTheDocument();
  });
});
