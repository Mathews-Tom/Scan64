import { test, expect } from '@playwright/test';
import { movePiece } from './board';
import { prepareToday } from './today';
import { makeGameReview } from '../../src/components/review.test-fixtures';

test.beforeEach(async ({ page }) => { await prepareToday(page); });

test('analysis board accepts a real pointer move before starting a player-owned game', async ({ page }) => {
  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
  });
  await page.route('**/v1/games', async route => {
    expect(route.request().postDataJSON().owner_color).toBe('black');
    await route.fulfill({ json: { id: 'test-game' } });
  });
  await page.route('**/v1/play-sessions', async route => {
    await route.fulfill({ json: { id: 'test-session', status: 'active' } });
  });

  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Review', exact: true }).click();
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
  await page.route(`**/v1/games/${gameId}/review`, async route => {
    authorization = route.request().headers().authorization ?? '';
    await route.fulfill({ json: makeGameReview({ game_id: gameId }) });
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
  await page.route(`**/v1/games/${gameId}/review`, async route => {
    await route.fulfill({ status: 404 });
  });
  await page.route(`**/v1/games/${gameId}/analysis-status`, async route => {
    await route.fulfill({ status: 404 });
  });

  await page.goto(`/games/${gameId}/analysis`);

  await expect(page.getByText('Game analysis was not found.')).toBeVisible();
  await expect(page.getByTestId('review-move-list').locator('li')).toHaveCount(0);
});

for (const source of ['lichess', 'chesscom']) {
  test(`imports ${source} only on submit and opens owned Review without a second job`, async ({ page }) => {
    const gameId = '00000000-0000-4000-8000-000000000088';
    let imports = 0;
    let jobs = 0;
    await page.route('**/v1/players/*/profile', route => route.fulfill({ json: { player_id: 'player-1', rating: 1500, display_name: 'Alice' } }));
    await page.route('**/v1/games/import-online', async route => {
      imports += 1;
      expect(route.request().headers().authorization).toBe('Bearer test-token');
      expect(route.request().postDataJSON()).toEqual({ source, username: 'Alice', max_games: 3 });
      await route.fulfill({ json: {
        imported_count: imports === 1 ? 1 : 0, duplicate_count: imports === 1 ? 0 : 1, skipped_count: 1,
        games: imports === 1 ? [{ id: gameId, white: 'Alice', black: 'Bob', result: '1-0', owner_color: 'white', analysis_job_id: 'job-88' }] : [],
        message: 'Skipped 1 non-standard game.',
      } });
    });
    await page.route('**/v1/analysis-jobs', async route => { jobs += 1; await route.fulfill({ status: 500 }); });
    await page.route(`**/v1/games/${gameId}/review`, async route => {
      expect(route.request().headers().authorization).toBe('Bearer test-token');
      await route.fulfill({ json: makeGameReview({ game_id: gameId }) });
    });
    await page.route(`**/v1/games/${gameId}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
    await page.goto('/import');
    await page.getByLabel('Source', { exact: true }).selectOption(source);
    await page.getByLabel('Username', { exact: true }).fill('Alice');
    await page.getByLabel('Number of games').fill('3');
    await expect(page.getByLabel('PGN game text')).toBeVisible();
    expect(imports).toBe(0);
    await page.getByRole('button', { name: 'Import online games', exact: true }).click();
    await expect(page.getByText('1 new · 0 duplicate · 1 skipped')).toBeVisible();
    await page.getByRole('button', { name: 'Review Alice vs Bob' }).click();
    await expect(page).toHaveURL(new RegExp(`/games/${gameId}/analysis$`));
    await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
    expect(jobs).toBe(0);
    await page.goBack();
    await page.getByLabel('Source', { exact: true }).selectOption(source);
    await page.getByLabel('Username', { exact: true }).fill('Alice');
    await page.getByLabel('Number of games').fill('3');
    await page.getByRole('button', { name: 'Import online games', exact: true }).click();
    await expect(page.getByText('0 new · 1 duplicate · 1 skipped')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Review Alice vs Bob' })).toHaveCount(0);
    expect(jobs).toBe(0);
  });
}
