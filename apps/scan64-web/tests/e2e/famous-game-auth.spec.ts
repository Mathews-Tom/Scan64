import { expect, test } from '@playwright/test';

test('Famous Games records predictions with the studying player bearer token', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('scan64_player_id', 'study-player');
    localStorage.setItem('scan64_player_token:study-player', 'study-token');
  });
  await page.route('**/v1/content/famous-games', async route => {
    await route.fulfill({ json: [{
      id: 'opera', skill_mapping: { 'tactics.development': 1 },
      payload: {
        title: 'Opera Game', historical_context: 'Paris, 1858.',
        strategic_context: 'Develop pieces.', moves: ['e4'],
        decisions: [{
          id: 'opening', ply: 0,
          fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
          prompt: 'Choose a move.', played_move: 'e4', accepted_moves: ['e4'],
          verified_alternatives: [], hints: [], comparison: 'e4 controls the centre.',
        }],
      },
    }] });
  });
  await page.route('**/v1/content/famous-games/opera/attempts', async route => {
    expect(route.request().headers().authorization).toBe('Bearer study-token');
    expect(route.request().postDataJSON()).toMatchObject({
      player_id: 'study-player', decision_id: 'opening', response_payload: { move: 'e4' },
    });
    await route.fulfill({ json: { id: 'attempt-1', success: true, hint_assisted: false } });
  });
  await page.goto('/');
  await page.getByRole('button', { name: 'Library', exact: true }).click();
  await page.getByText('Famous Games', { exact: true }).click();
  await page.getByRole('button', { name: 'Opera Game' }).click();
  await page.getByLabel('Your move').fill('e4');
  await page.getByRole('button', { name: 'Submit', exact: true }).click();
  await expect(page.getByText('Correct.', { exact: true })).toBeVisible();
});
