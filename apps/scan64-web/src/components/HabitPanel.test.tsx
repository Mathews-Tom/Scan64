import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import type { DiagnosisPatternRead, HabitSummaryRead, HabitTargetRead, PatternsReport } from '../api/types';
import { HabitPanel } from './HabitPanel';

const pattern: DiagnosisPatternRead = {
  diagnosis: 'board_awareness.hanging_piece', name: 'Hanging piece', template: 'A piece is left undefended.',
  occurrence_count: 3, game_ids: ['one', 'two', 'three'], evidence_references: ['evidence'],
  total_occurrences: 4, scored_occurrences: 3, observed_loss: 0.6,
  supporting_moves: [{ game_id: 'one', ply: 7, lesson_id: 'lesson-one', loss: 0.2 }],
};
const patterns: PatternsReport = {
  player_id: 'owner', minimum_occurrences: 3, eligible_game_count: 5,
  status: 'recurring_diagnosis', recurring_diagnoses: [pattern],
};
const target: HabitTargetRead = {
  id: 'target-one', code: pattern.diagnosis, status: 'practicing', practice_count: 1,
  practice_game_count: 1, eligible_window_count: 0, invalidity_reason: null, award: null, pattern_snapshot: pattern,
  blocking_game: null,
};
const summary: HabitSummaryRead = {
  utc_day: '2026-11-13', goal: 3, qualifying_solves: 1, remaining: 2,
  pending_goal: null, pending_effective_day: null, current_streak: 2, best_streak: 4,
  reset_at: '2026-11-14T00:00:00Z', day_status: 'in_progress',
  days: [{ utc_day: '2026-11-12', goal: 3, qualifying_solves: 3, met: true, withdrawn_sources: 0 }],
  supply: { remaining_daily_slots: 0, eligible_lesson_retries: 1, extra_modes_available: false },
  targets: [], evidence_limits: 'Only post-cutover evidence under habit-v1 qualifies.',
};

beforeEach(() => {
  vi.spyOn(ApiClient, 'getPlayerHabit').mockResolvedValue(summary);
  vi.spyOn(ApiClient, 'getPuzzleModes').mockResolvedValue({ themes: [], streak: { current: 7, best: 9, reset_reason: null } });
  vi.spyOn(ApiClient, 'updateHabitGoal');
  vi.spyOn(ApiClient, 'startHabitTarget');
  vi.spyOn(ApiClient, 'refreshHabitTarget');
});
afterEach(() => vi.restoreAllMocks());

describe('HabitPanel', () => {
  it('identifies an unfinished blocking game with an explicit resume-or-resign link', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, targets: [{
      ...target, status: 'observing', blocking_game: {
        game_id: 'blocked-game', session_id: 'blocked-session', reason: 'unfinished',
      },
    }] });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    expect(await screen.findByText(/Waiting for this unfinished game/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Resume or resign game blocked-game' }))
      .toHaveAttribute('href', '/play?session=blocked-session');
  });

  it('shows the server UTC goal, short supply and separate calendar and solve streaks without writes', async () => {
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' });
    expect(screen.getByText(/1\/3 qualifying solves · 2 remaining/)).toBeInTheDocument();
    expect(screen.getByText(/0 unplayed daily slots · 1 eligible lesson retries · Extra puzzle modes unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/Short supply never lowers your goal/)).toBeInTheDocument();
    expect(screen.getByText(/2 current · 4 best UTC days/)).toBeInTheDocument();
    expect(screen.getByText(/7 current · 9 best consecutive qualifying puzzle solves/)).toBeInTheDocument();
    expect(screen.getByText(/2026-11-14T00:00:00Z/)).toHaveAttribute('dateTime', '2026-11-14T00:00:00Z');
    expect(ApiClient.getPlayerHabit).toHaveBeenCalledWith('owner');
    expect(ApiClient.updateHabitGoal).not.toHaveBeenCalled();
    expect(ApiClient.startHabitTarget).not.toHaveBeenCalled();
    expect(ApiClient.refreshHabitTarget).not.toHaveBeenCalled();
  });

  it('saves a positive goal for the next UTC day while preserving today’s goal', async () => {
    vi.mocked(ApiClient.updateHabitGoal).mockResolvedValue({ ...summary, pending_goal: 5, pending_effective_day: '2026-11-14' });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    fireEvent.change(await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' }), { target: { value: '5' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save next-day goal' }));
    expect(await screen.findByText('Scheduled goal: 5 qualifying solves from 2026-11-14 UTC.')).toBeInTheDocument();
    expect(ApiClient.updateHabitGoal).toHaveBeenCalledTimes(1);
    expect(ApiClient.updateHabitGoal).toHaveBeenCalledWith('owner', 5);
    expect(screen.getByText(/1\/3 qualifying solves · 2 remaining/)).toBeInTheDocument();
    expect(screen.getByText('Goal saved for the next UTC day. Today’s goal is unchanged.')).toHaveFocus();
  });

  it.each(['0', '-1', '1.5'])('rejects invalid goal %s without an API write and focuses the alert', async value => {
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    const input = await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' });
    fireEvent.change(input, { target: { value } });
    const form = input.closest('form');
    if (!form) throw new Error('Goal form missing');
    fireEvent.submit(form);
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.getByText(/Enter a positive whole-number goal/)).toBeInTheDocument();
    expect(ApiClient.updateHabitGoal).not.toHaveBeenCalled();
  });

  it('keeps today in progress and withdrawn historical days met', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, qualifying_solves: 3, remaining: 0,
      days: [{ ...summary.days[0], withdrawn_sources: 2 }, { utc_day: summary.utc_day, goal: 3, qualifying_solves: 3, met: true, withdrawn_sources: 0 }],
    });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    const history = await screen.findByRole('list', { name: 'UTC day history' });
    expect(within(history).getByText(/2026-11-12/).closest('li')).toHaveTextContent('3/3 · Met');
    expect(within(history).getByText(/2 withdrawn source/)).toHaveTextContent('historical met status preserved');
    expect(within(history).getByText(/2026-11-13/).closest('li')).toHaveTextContent('Goal reached; in progress');
    expect(screen.getByText(/Today is in progress until/)).toBeInTheDocument();
  });

  it('starts only the currently qualifying pattern through an explicit action on its card', async () => {
    vi.mocked(ApiClient.startHabitTarget).mockResolvedValue({ ...summary, targets: [target] });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    const button = await screen.findByRole('button', { name: 'Track practice target for Hanging piece' });
    expect(within(screen.getByRole('region', { name: 'Recurring pattern' })).getByRole('button', { name: button.textContent ?? '' })).toBe(button);
    fireEvent.click(button);
    expect(await screen.findByText(/Practice evidence: 1\/3/)).toBeInTheDocument();
    expect(ApiClient.startHabitTarget).toHaveBeenCalledTimes(1);
    expect(ApiClient.startHabitTarget).toHaveBeenCalledWith('owner', pattern.diagnosis);
    expect(screen.queryByRole('button', { name: 'Track practice target for Hanging piece' })).not.toBeInTheDocument();
    expect(screen.getByText(/first recorded opportunity attempt and app-reported zero hints/)).toBeInTheDocument();
    expect(screen.getByText(/Diagnosis uses a ≥150cp fast-pass swing threshold/)).toBeInTheDocument();
  });

  it('does not offer tracking for insufficient or nonrecurring evidence', async () => {
    const { rerender } = render(<HabitPanel playerId="owner" patterns={{ ...patterns, status: 'insufficient_data', recurring_diagnoses: [] }} />);
    await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' });
    expect(screen.queryByRole('button', { name: /Track practice target/ })).not.toBeInTheDocument();
    rerender(<HabitPanel playerId="owner" patterns={{ ...patterns, status: 'no_recurring_diagnosis', recurring_diagnoses: [] }} />);
    expect(screen.queryByRole('button', { name: /Track practice target/ })).not.toBeInTheDocument();
    expect(ApiClient.startHabitTarget).not.toHaveBeenCalled();
  });

  it('retains tracked patterns and saved coverage when the top pattern changes or disappears', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, targets: [target] });
    const { rerender } = render(<HabitPanel playerId="owner" patterns={patterns} />);
    await screen.findByText(/Practice evidence: 1\/3/);
    const next = { ...pattern, diagnosis: 'tactics.fork.knight', name: 'Knight fork' };
    rerender(<HabitPanel playerId="owner" patterns={{ ...patterns, recurring_diagnoses: [next] }} />);
    const tracked = screen.getByRole('region', { name: 'Tracked pattern: Hanging piece' });
    expect(within(tracked).getByText(/Coverage at start: 3\/4/)).toBeInTheDocument();
    expect(within(tracked).getByRole('link', { name: 'Game one, ply 7' })).toHaveAttribute('href', '/games/one/analysis?ply=7');
    expect(screen.getByRole('button', { name: 'Track practice target for Knight fork' })).toBeInTheDocument();
    rerender(<HabitPanel playerId="owner" patterns={{ ...patterns, status: 'no_recurring_diagnosis', recurring_diagnoses: [] }} />);
    expect(screen.getByRole('region', { name: 'Tracked pattern: Hanging piece' })).toBeInTheDocument();
    expect(ApiClient.startHabitTarget).not.toHaveBeenCalled();
  });

  it('keeps the award historical with separate bounded absence wording and current invalidity', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, targets: [{ ...target, status: 'invalidated',
      practice_count: 3, practice_game_count: 2, eligible_window_count: 5,
      invalidity_reason: 'Supporting lesson retired', award: { awarded_at: '2026-11-12T12:00:00Z', game_ids: ['g1', 'g2', 'g3', 'g4', 'g5'] },
    }] });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    expect(await screen.findByText('Pattern practice target met')).toBeInTheDocument();
    expect(screen.getByText(/At the time of the award: not observed in the last five eligible games/)).toBeInTheDocument();
    expect(screen.getByText(/\(historical award\)/)).toBeInTheDocument();
    expect(screen.getByText(/Current evidence unavailable: Supporting lesson retired/)).toBeInTheDocument();
    expect(screen.getByText(/Absence evidence: 5\/5/)).toBeInTheDocument();
    expect(screen.getByText(/uncapped non-retired observation restarts the window/)).toBeInTheDocument();
    expect(screen.getByText(/not a cure or permanent mastery/)).toBeInTheDocument();
    expect(screen.queryByText(/^Cleared$/)).not.toBeInTheDocument();
    expect(ApiClient.refreshHabitTarget).not.toHaveBeenCalled();
  });

  it('persists invalidity only through explicit tracked-evidence refresh', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, targets: [target] });
    vi.mocked(ApiClient.refreshHabitTarget).mockResolvedValue({ ...summary, targets: [{ ...target, status: 'invalidated', invalidity_reason: 'Evidence withdrawn' }] });
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh tracked evidence for Hanging piece' }));
    expect(await screen.findByText(/Current evidence unavailable: Evidence withdrawn/)).toBeInTheDocument();
    expect(ApiClient.refreshHabitTarget).toHaveBeenCalledTimes(1);
    expect(ApiClient.refreshHabitTarget).toHaveBeenCalledWith('owner', 'target-one');
    expect(screen.getByRole('list', { name: 'UTC day history' })).toHaveTextContent('3/3 · Met');
  });

  it('focuses a failed asynchronous write and keeps progress available for retry', async () => {
    vi.mocked(ApiClient.updateHabitGoal).mockRejectedValue(new Error('Connection lost'));
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Save next-day goal' }));
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.getByText(/Habit change could not be saved: Connection lost/)).toBeInTheDocument();
    expect(screen.getByText(/1\/3 qualifying solves · 2 remaining/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save next-day goal' })).toBeEnabled();
  });

  it('focuses a failed read and retries without starting practice or awarding evidence', async () => {
    vi.mocked(ApiClient.getPlayerHabit).mockRejectedValueOnce(new Error('Offline')).mockResolvedValue(summary);
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    expect(await screen.findByRole('alert')).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Retry habit record' }));
    expect(await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' })).toBeInTheDocument();
    expect(ApiClient.getPlayerHabit).toHaveBeenCalledTimes(2);
    expect(ApiClient.startHabitTarget).not.toHaveBeenCalled();
    expect(ApiClient.refreshHabitTarget).not.toHaveBeenCalled();
  });

  it('does not hide the daily goal if the separate solve streak cannot load', async () => {
    vi.mocked(ApiClient.getPuzzleModes).mockRejectedValue(new Error('Mode service unavailable'));
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    await screen.findByRole('spinbutton', { name: 'Qualifying solves per day' });
    expect(await screen.findByRole('alert')).toHaveFocus();
    expect(screen.getByText(/Puzzle solve streak unavailable: Mode service unavailable/)).toBeInTheDocument();
    expect(screen.getByText(/1\/3 qualifying solves · 2 remaining/)).toBeInTheDocument();
  });

  it('disables habit writes while a request is pending', async () => {
    const { promise, resolve } = Promise.withResolvers<HabitSummaryRead>();
    vi.mocked(ApiClient.updateHabitGoal).mockReturnValue(promise);
    render(<HabitPanel playerId="owner" patterns={patterns} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Save next-day goal' }));
    expect(screen.getByRole('button', { name: 'Saving habit change…' })).toBeDisabled();
    expect(screen.getByRole('spinbutton', { name: 'Qualifying solves per day' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Track practice target for Hanging piece' })).toBeDisabled();
    resolve(summary);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save next-day goal' })).toBeEnabled());
  });

  it('ignores stale goal responses when the owner changes', async () => {
    const { promise, resolve } = Promise.withResolvers<HabitSummaryRead>();
    vi.mocked(ApiClient.updateHabitGoal).mockReturnValue(promise);
    const { rerender } = render(<HabitPanel playerId="owner" patterns={patterns} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Save next-day goal' }));
    vi.mocked(ApiClient.getPlayerHabit).mockResolvedValue({ ...summary, goal: 8, qualifying_solves: 0, remaining: 8 });
    rerender(<HabitPanel playerId="other-owner" patterns={{ ...patterns, player_id: 'other-owner' }} />);
    await screen.findByDisplayValue('8');
    resolve({ ...summary, pending_goal: 99, pending_effective_day: '2026-11-14' });
    await waitFor(() => expect(screen.getByRole('spinbutton', { name: 'Qualifying solves per day' })).toHaveValue(8));
    expect(screen.queryByText(/Scheduled goal: 99/)).not.toBeInTheDocument();
  });
});
