import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { PatternRecurrenceReport } from '../api/types';
import { PatternRecurrencePanel } from './PatternRecurrencePanel';

export const recurrenceFixture: PatternRecurrenceReport = {
  player_id: 'owner', minimum_games_per_period: 3, minimum_owner_moves_per_game: 20,
  coverage: { owned_saved_games: 8, ambiguous_played_time: 2 },
  caveat: 'Descriptive association only. 95% Wilson intervals assume independent owner moves; moves within games are correlated. Training did not necessarily cause a change.',
  patterns: [{
    code: 'threat_processing.missed_capture', name: 'Missed capture', first_training_at: '2026-10-10T12:00:00Z',
    status: 'comparison', reason: 'Enough analysed games.',
    before: { candidate_games: 3, games: 3, owner_moves: 60, occurrences: 21, per_100_moves: 35, wilson_95_per_100: [24.16, 47.63], excluded: {} },
    after: { candidate_games: 4, games: 3, owner_moves: 60, occurrences: 3, per_100_moves: 5, wilson_95_per_100: [1.72, 13.7], excluded: { unsupported_candidate_evidence: 1 } },
  }],
};

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('before/after Progress', () => {
  it('shows real counts, per100 intervals and descriptive coverage with no improvement claim', async () => {
    vi.spyOn(ApiClient, 'getPatternRecurrence').mockResolvedValue(recurrenceFixture);
    render(<PatternRecurrencePanel playerId="owner" />);
    const pattern = await screen.findByRole('region', { name: 'Missed capture' });
    expect(within(pattern).getByText('Before/after comparison')).toBeInTheDocument();
    const before = within(pattern).getByRole('region', { name: 'Before training' });
    expect(before).toHaveTextContent('3 games · 60 owner moves · 21 occurrences');
    expect(before).toHaveTextContent('35.00 occurrences per 100 owner moves');
    expect(before).toHaveTextContent('95% Wilson interval: 24.16–47.63 per 100 owner moves');
    expect(screen.getByText(/assume independent owner moves/)).toHaveTextContent('correlated');
    expect(within(pattern).getByText('unsupported candidate evidence: 1')).toBeInTheDocument();
    fireEvent.click(screen.getByText('Library coverage and exclusions'));
    expect(screen.getByText('ambiguous played time: 2')).toBeInTheDocument();
  });

  it('leaves insufficient rates unavailable rather than displaying zeros', async () => {
    const row = recurrenceFixture.patterns[0];
    vi.spyOn(ApiClient, 'getPatternRecurrence').mockResolvedValue({ ...recurrenceFixture, patterns: [{
      ...row, status: 'not_enough_games', first_training_at: null,
      reason: 'No completed server-verified mapped training since the learning reset.',
      before: { ...row.before, games: 0, owner_moves: 0, occurrences: null, per_100_moves: null, wilson_95_per_100: null },
      after: { ...row.after, games: 0, owner_moves: 0, occurrences: null, per_100_moves: null, wilson_95_per_100: null },
    }] });
    render(<PatternRecurrencePanel playerId="owner" />);
    expect(await screen.findByText('Not enough games')).toBeInTheDocument();
    expect(screen.getAllByText('Rate unavailable')).toHaveLength(2);
    expect(screen.getAllByText(/Occurrences unavailable/)).toHaveLength(2);
    expect(screen.queryByText(/· 0 occurrences/)).not.toBeInTheDocument();
    expect(screen.queryByText(/0.00 occurrences/)).not.toBeInTheDocument();
    expect(screen.getByText(/No completed server-verified/)).toBeInTheDocument();
  });

  it('keeps failure and retry local and discards another owner’s late response', async () => {
    const old = Promise.withResolvers<PatternRecurrenceReport>();
    const read = vi.spyOn(ApiClient, 'getPatternRecurrence')
      .mockReturnValueOnce(old.promise)
      .mockRejectedValueOnce(new Error('offline'))
      .mockResolvedValueOnce(recurrenceFixture);
    const view = render(<><p>Other Profile panel</p><PatternRecurrencePanel playerId="old-owner" /></>);
    view.rerender(<><p>Other Profile panel</p><PatternRecurrencePanel playerId="owner" /></>);
    expect(await screen.findByRole('alert')).toHaveTextContent('offline');
    old.resolve({ ...recurrenceFixture, player_id: 'old-owner' });
    expect(screen.getByText('Other Profile panel')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry pattern comparison' }));
    expect(await screen.findByText('Before/after comparison')).toBeInTheDocument();
    expect(read).toHaveBeenLastCalledWith('owner');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});
