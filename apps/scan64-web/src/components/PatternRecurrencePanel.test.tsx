import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
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

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

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

  it('downloads exactly one ZIP only on click, shows loading and recovers locally from failure', async () => {
    vi.spyOn(ApiClient, 'getPatternRecurrence').mockResolvedValue(recurrenceFixture);
    const pending = Promise.withResolvers<Blob>();
    const packet = new Blob(['packet bytes'], { type: 'application/zip' });
    const download = vi.spyOn(ApiClient, 'getCoachPacket')
      .mockReturnValueOnce(pending.promise)
      .mockRejectedValueOnce(new Error('local server unavailable'))
      .mockResolvedValueOnce(packet);
    const create = vi.fn().mockReturnValue('blob:coach-packet');
    const revoke = vi.fn();
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
    const clicked: Array<{ href: string; filename: string }> = [];
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      clicked.push({ href: this.href, filename: this.download });
    });
    render(<PatternRecurrencePanel playerId="owner" />);
    await screen.findByText('Before/after comparison');
    expect(download).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Download coach packet' }));
    expect(screen.getByRole('button', { name: 'Preparing coach packet…' })).toBeDisabled();
    expect(create).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(packet); });
    expect(download).toHaveBeenCalledExactlyOnceWith('owner', expect.any(AbortSignal));
    expect(create).toHaveBeenCalledExactlyOnceWith(packet);
    expect(clicked).toEqual([{ href: 'blob:coach-packet', filename: 'scan64-coach-packet.zip' }]);
    expect(revoke).toHaveBeenCalledExactlyOnceWith('blob:coach-packet');
    expect(document.querySelector('a[download]')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Download coach packet' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Coach packet unavailable: local server unavailable');
    expect(screen.getByText('Before/after comparison')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Download coach packet' }));
    await waitFor(() => expect(clicked).toHaveLength(2));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('aborts and discards old-owner and unmounted downloads even if the transport resolves late', async () => {
    vi.spyOn(ApiClient, 'getPatternRecurrence').mockResolvedValue(recurrenceFixture);
    const old = Promise.withResolvers<Blob>();
    const unmounted = Promise.withResolvers<Blob>();
    const download = vi.spyOn(ApiClient, 'getCoachPacket')
      .mockReturnValueOnce(old.promise).mockReturnValueOnce(unmounted.promise);
    const create = vi.fn();
    vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: vi.fn() });
    const view = render(<PatternRecurrencePanel playerId="old-owner" />);
    fireEvent.click(screen.getByRole('button', { name: 'Download coach packet' }));
    const oldSignal = download.mock.calls[0][1]!;
    view.rerender(<PatternRecurrencePanel playerId="owner" />);
    expect(oldSignal.aborted).toBe(true);
    expect(screen.getByRole('button', { name: 'Download coach packet' })).toBeEnabled();
    await act(async () => { old.resolve(new Blob(['old owner'])); });
    expect(create).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Download coach packet' }));
    const currentSignal = download.mock.calls[1][1]!;
    view.unmount();
    expect(currentSignal.aborted).toBe(true);
    await act(async () => { unmounted.resolve(new Blob(['unmounted owner'])); });
    expect(create).not.toHaveBeenCalled();
  });
});
