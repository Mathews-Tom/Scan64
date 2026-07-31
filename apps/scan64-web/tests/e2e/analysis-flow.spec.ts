import { test, expect } from '@playwright/test';
import { movePiece } from './board';

test('analysis board accepts a real pointer move before starting a player-owned game', async ({ page }) => {
  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
  });
  await page.route('**/v1/games', async route => {
    await route.fulfill({ json: { id: 'test-game' } });
  });
  await page.route('**/v1/play-sessions', async route => {
    await route.fulfill({ json: { id: 'test-session', status: 'active' } });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Analysis Board' }).click();
  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();

  const fenInput = page.getByPlaceholder('Paste FEN here');
  await fenInput.fill('rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2');
  await page.getByRole('button', { name: 'Load FEN' }).click();
  await movePiece(page, page.locator('.cg-wrap'), 'e4', 'e5');
  await expect(fenInput).toHaveValue(
    'rnbqkbnr/pp1ppppp/8/2p1P3/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2',
  );

  await page.getByTestId('play-from-here').click();
  await expect(page.getByRole('heading', { name: 'Play against Scan64' })).toBeVisible();
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
});

test('opens an owned analysis deep link with active-player authorization', async ({ page }) => {
  const gameId = '00000000-0000-0000-0000-000000000001';
  let authorization = '';
  await page.addInitScript(() => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'test-token');
  });
  await page.route(`**/v1/games/${gameId}/positions`, async route => {
    authorization = route.request().headers().authorization ?? '';
    await route.fulfill({ json: [] });
  });
  await page.route(`**/v1/games/${gameId}/analysis-status`, async route => {
    await route.fulfill({ json: { status: 'completed' } });
  });

  await page.goto(`/games/${gameId}/analysis`);

  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
  await expect.poll(() => authorization).toBe('Bearer test-token');
});

test('renders a non-disclosing not-found state for a non-owned analysis deep link', async ({ page }) => {
  const gameId = '00000000-0000-0000-0000-000000000002';
  await page.addInitScript(() => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'test-token');
  });
  await page.route(`**/v1/games/${gameId}/positions`, async route => {
    await route.fulfill({ status: 404 });
  });
  await page.route(`**/v1/games/${gameId}/analysis-status`, async route => {
    await route.fulfill({ status: 404 });
  });

  await page.goto(`/games/${gameId}/analysis`);

  await expect(page.getByText('Game analysis was not found.')).toBeVisible();
  await expect(page.getByTestId('position-list').locator('li')).toHaveCount(0);
});
