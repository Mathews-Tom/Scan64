import { expect, test } from '@playwright/test';
import { makeGameReview } from '../../src/components/review.test-fixtures';
import { prepareToday } from './today';

test.beforeEach(async ({ page }) => { await prepareToday(page); });

test('Profile shows unavailable figures and separate scored and clock coverage', async ({ page }) => {
  await page.route('**/v1/players/*/progress', route => route.fulfill({ json: { player_id: 'player-1', skills: [] } }));
  await page.route('**/v1/players/*/evidence', route => route.fulfill({ json: { player_id: 'player-1', evidence_items: [] } }));
  await page.goto('/profile');
  await expect(page.getByText(/Overall: 86.0%/)).toContainText('5 / 8 scored / eligible owner moves');
  await expect(page.getByText(/Caro-Kann Defense: Unavailable/)).toContainText('0 / 1 scored / eligible owner moves');
  await expect(page.getByText(/real-time TimeControl/)).toContainText('clock coverage: 4 / 7');
});

test('Good moment selects the actual board while mistake retries remain available', async ({ page }) => {
  const gameId = '11111111-1111-4111-8111-111111111111';
  const review = makeGameReview({ game_id: gameId, good_moments: [{
    ply: 7, san: 'Qxf7#', skill_id: 'threat_processing.missed_check',
    explanation: 'The stored source analysis proves mate.', line: ['h5f7'],
  }] });
  await page.route(`**/v1/games/${gameId}/review`, route => route.fulfill({ json: review }));
  await page.route(`**/v1/games/${gameId}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  await page.goto(`/games/${gameId}/analysis`);
  await page.getByRole('button', { name: 'Good moment at ply 7: Qxf7#' }).click();
  await expect(page.getByTestId('review-move-7')).toHaveAttribute('aria-current', 'step');
  await expect(page.getByTestId('review-selected-position')).toContainText('Qxf7# (final position)');
  const board = page.getByTestId('review-board');
  await expect.poll(() => board.locator('piece.white.queen').evaluateAll(pieces =>
    pieces.some(piece => (piece as HTMLElement & { cgKey?: string }).cgKey === 'f7'),
  )).toBe(true);
  await expect(page.getByRole('button', { name: 'Retry this moment' })).toBeVisible();
  await page.getByRole('button', { name: 'Key moment at ply 3' }).click();
  await expect(page.getByTestId('review-move-3')).toHaveAttribute('aria-current', 'step');
});
