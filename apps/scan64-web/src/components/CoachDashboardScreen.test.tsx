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
        recurring_habits: [
          {
            rule_id: 'early-queen-moves',
            description: 'Moves the queen early in open games',
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
    expect(screen.getByText('Moves the queen early in open games')).toBeInTheDocument();
    expect(screen.getByText('Missed a knight fork')).toBeInTheDocument();
    expect(screen.getByText('tactical-motif')).toBeInTheDocument();
    expect(screen.getByText('scan64 · 1')).toBeInTheDocument();
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
