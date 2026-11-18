import { expect, test } from '@playwright/test';
import { prepareToday } from './today';
import type { PatternRecurrenceReport } from '../../src/api/types';

const report: PatternRecurrenceReport = {
  player_id: 'player-1', minimum_games_per_period: 3, minimum_owner_moves_per_game: 20,
  coverage: { owned_saved_games: 6, ambiguous_played_time: 2 },
  caveat: 'Descriptive association only, not causal. 95% Wilson intervals assume independent owner moves; moves within games are correlated.',
  patterns: [{ code: 'threat_processing.missed_capture', name: 'Missed capture',
    first_training_at: '2026-10-10T12:00:00Z', status: 'comparison', reason: 'Enough analysed games.',
    before: { candidate_games: 3, games: 3, owner_moves: 60, occurrences: 21, per_100_moves: 35, wilson_95_per_100: [24.16, 47.63], excluded: {} },
    after: { candidate_games: 4, games: 3, owner_moves: 60, occurrences: 3, per_100_moves: 5, wilson_95_per_100: [1.72, 13.7], excluded: { unsupported_candidate_evidence: 1 } },
  }],
};

test('Progress comparison, local retry and insufficient evidence preserve Profile', async ({ page }) => {
  await prepareToday(page);
  await page.route('**/v1/players/*/progress', route => route.fulfill({ json: { player_id: 'player-1', skills: [] } }));
  await page.route('**/v1/players/*/evidence', route => route.fulfill({ json: { player_id: 'player-1', evidence_items: [] } }));
  let state: 'error' | 'comparison' | 'insufficient' = 'error';
  await page.route('**/v1/players/*/progress/pattern-recurrence', route => {
    if (state === 'error') return route.fulfill({ status: 503, json: { detail: 'Temporarily unavailable' } });
    const row = report.patterns[0];
    return route.fulfill({ json: state === 'comparison' ? report : { ...report, patterns: [{
      ...row, status: 'not_enough_games', reason: 'Not enough games with supported evidence.',
      before: { ...row.before, games: 2, owner_moves: 40, occurrences: 14, per_100_moves: null, wilson_95_per_100: null, excluded: { unsupported_candidate_evidence: 1 } },
      after: { ...row.after, per_100_moves: null, wilson_95_per_100: null },
    }] } });
  });
  await page.goto('/profile');
  const progress = page.getByRole('region', { name: 'Progress: before/after patterns', exact: true });
  await expect(progress.getByRole('alert')).toContainText('Pattern comparison unavailable');
  await expect(page.getByRole('heading', { name: 'Your game insights' })).toBeVisible();
  state = 'comparison';
  await progress.getByRole('button', { name: 'Retry pattern comparison' }).click();
  await expect(progress.getByText('35.00 occurrences per 100 owner moves')).toBeVisible();
  await expect(progress.getByText('95% Wilson interval: 24.16–47.63 per 100 owner moves')).toBeVisible();
  await expect(progress.getByText('3 games · 60 owner moves · 21 occurrences')).toBeVisible();
  await progress.getByText('Library coverage and exclusions').click();
  await expect(progress.getByText('ambiguous played time: 2')).toBeVisible();
  state = 'insufficient';
  await progress.getByRole('button', { name: 'Refresh pattern comparison' }).click();
  await expect(progress.getByText('Not enough games', { exact: true })).toBeVisible();
  await expect(progress.getByText('Rate unavailable')).toHaveCount(2);
  await expect(progress.getByText('unsupported candidate evidence: 1')).toHaveCount(2);
  await expect(page.getByRole('heading', { name: 'Your game insights' })).toBeVisible();
});

for (const evidenceState of ['failed', 'pending'] as const) {
  test(`recurrence retries independently when parent evidence is ${evidenceState}`, async ({ page }) => {
    await prepareToday(page);
    await page.route('**/v1/players/*/progress', route => route.fulfill({ json: { player_id: 'player-1', skills: [] } }));
    await page.route('**/v1/players/*/evidence', route => evidenceState === 'failed'
      ? route.fulfill({ status: 503, json: { detail: 'Evidence unavailable' } })
      : undefined);
    let recurrenceFailed = true;
    await page.route('**/v1/players/*/progress/pattern-recurrence', route => route.fulfill(recurrenceFailed
      ? { status: 503, json: { detail: 'Temporarily unavailable' } }
      : { json: report }));
    await page.goto('/profile');
    const progress = page.getByRole('region', { name: 'Progress: before/after patterns', exact: true });
    const parent = page.getByRole('region', { name: 'Profile reports', exact: true });
    await expect(parent).toContainText(evidenceState === 'failed' ? 'Profile unavailable:' : 'Loading profile...');
    await expect(progress.getByRole('alert')).toContainText('Pattern comparison unavailable');
    recurrenceFailed = false;
    await progress.getByRole('button', { name: 'Retry pattern comparison' }).click();
    await expect(progress.getByText('35.00 occurrences per 100 owner moves')).toBeVisible();
    await expect(progress).toHaveCount(1);
    await expect(parent).toContainText(evidenceState === 'failed' ? 'Profile unavailable:' : 'Loading profile...');
  });
}
