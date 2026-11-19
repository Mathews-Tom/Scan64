import { test, expect } from '@playwright/test';
import { movePiece } from './board';
import { prepareToday } from './today';
import { makeGameReview } from '../../src/components/review.test-fixtures';
import { Chess } from 'chess.js';
import { readFile } from 'node:fs/promises';

test.beforeEach(async ({ page }) => {
  await prepareToday(page);
  await page.route('**/v1/analysis/position', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    const game = new Chess(fen);
    const lines = game.isGameOver() ? [] : game.moves({ verbose: true }).slice(0, 3).map(move => ({
      score_cp: 25, mate: null,
      moves: [{ uci: `${move.from}${move.to}${move.promotion ?? ''}`, san: move.san }],
    }));
    await route.fulfill({ json: { fen: game.fen(), lines } });
  });
});

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
    const request = route.request().postDataJSON() as { player_id: string };
    await route.fulfill({ json: { id: 'test-session', player_id: request.player_id,
      opponent_config: {}, coach_mode: false, assistance_modes: [], status: 'active' } });
  });

  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Review', exact: true }).click();
  await page.getByRole('button', { name: 'Analysis Board' }).click();
  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();

  const fenInput = page.getByPlaceholder('Paste FEN here');
  await fenInput.fill('rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2');
  await page.getByRole('button', { name: 'Load FEN' }).click();
  await expect(page.getByText('Position loaded. Started a new variation tree.')).toBeVisible();
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

test('shows three White-perspective engine lines and commits a clicked PV prefix on a narrow screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const initialFen = new Chess().fen();
  let authorization = '';
  await page.route('**/v1/analysis/position', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    if (fen !== initialFen) return route.fallback();
    authorization = route.request().headers().authorization ?? '';
    await route.fulfill({ json: { fen, lines: [
      { score_cp: 42, mate: null, moves: [{ uci: 'e2e4', san: 'e4' }, { uci: 'e7e5', san: 'e5' }] },
      { score_cp: -20, mate: null, moves: [{ uci: 'd2d4', san: 'd4' }, { uci: 'd7d5', san: 'd5' }] },
      { score_cp: null, mate: -7, moves: [{ uci: 'g1f3', san: 'Nf3' }, { uci: 'd7d5', san: 'd5' }, { uci: 'g2g3', san: 'g3' }] },
    ] } });
  });
  await page.goto('/analysis');
  const engine = page.getByTestId('multipv-lines');
  await expect(engine.getByLabel('Line 1 evaluation +0.42')).toBeVisible();
  await expect(engine.getByLabel('Line 2 evaluation -0.20')).toBeVisible();
  await expect(engine.getByLabel('Line 3 evaluation −M7')).toBeVisible();
  expect(authorization).toBe('Bearer test-token');
  await engine.getByRole('button', { name: 'Play line 3 through 3: g3' }).click();
  const game = new Chess();
  game.move('Nf3'); game.move('d5'); game.move('g3');
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(game.fen());
  await expect(page.getByRole('list', { name: 'Retained variations' }).getByRole('button', { name: '2. g3' })).toHaveAttribute('aria-current', 'step');
  await expect(engine.getByLabel('Line 1 evaluation +0.25')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('retains sibling lines and downloads setup PGN with black-to-move variations', async ({ page }) => {
  await page.goto('/analysis');
  const fen = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 17';
  await page.getByLabel('FEN', { exact: true }).fill(fen);
  await page.getByRole('button', { name: 'Load FEN' }).click();
  await expect(page.getByText('Position loaded. Started a new variation tree.')).toBeVisible();
  await movePiece(page, page.locator('.cg-wrap'), 'e7', 'e5');
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(
    'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 18',
  );
  await movePiece(page, page.locator('.cg-wrap'), 'g1', 'f3');
  await expect(page.getByRole('button', { name: 'Make move' })).toBeEnabled();
  await page.getByRole('button', { name: 'First', exact: true }).click();
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(fen);
  await movePiece(page, page.locator('.cg-wrap'), 'c7', 'c5');
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(
    'rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 18',
  );
  await movePiece(page, page.locator('.cg-wrap'), 'b1', 'c3');
  const variations = page.getByRole('list', { name: 'Retained variations' });
  await expect(variations.getByRole('button', { name: '18. Nf3' })).toBeVisible();
  await expect(variations.getByRole('button', { name: 'Variation: 18. Nc3' })).toHaveAttribute('aria-current', 'step');
  await variations.getByRole('button', { name: '17... e5' }).click();
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(
    'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 18',
  );
  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download PGN' }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe('analysis.pgn');
  const path = await download.path();
  expect(path).not.toBeNull();
  const pgn = await readFile(path!, 'utf8');
  expect(pgn).toContain('[SetUp "1"]');
  expect(pgn).toContain(`[FEN "${fen}"]`);
  expect(pgn).toContain('17... e5 (17... c5 18. Nc3) 18. Nf3 *');
});

test('keeps the board usable and requires manual retry after a busy engine response', async ({ page }) => {
  let requests = 0;
  await page.route('**/v1/analysis/position', async route => {
    requests += 1;
    if (requests > 1) return route.fallback();
    await route.fulfill({ status: 503, json: { detail: 'Engine busy. Retry shortly.' } });
  });
  await page.goto('/analysis');
  await expect(page.getByRole('alert')).toContainText('Engine busy or unavailable.');
  await expect(page.getByRole('button', { name: 'Make move' })).toBeEnabled();
  expect(requests).toBe(1);
  await page.getByRole('button', { name: 'Retry', exact: true }).click();
  await expect(page.getByLabel('Line 1 evaluation +0.25')).toBeVisible();
  expect(requests).toBe(2);
  await movePiece(page, page.locator('.cg-wrap'), 'e2', 'e4');
  await expect(page.getByLabel('FEN', { exact: true })).toHaveValue(
    'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
  );
});

test('ignores stale analysis after A to B to A navigation', async ({ page }) => {
  const releases = Array.from({ length: 3 }, () => Promise.withResolvers<void>());
  const completed = Array.from({ length: 3 }, () => Promise.withResolvers<void>());
  let requests = 0;
  await page.route('**/v1/analysis/position', async route => {
    const index = requests++;
    if (index >= releases.length) return route.fallback();
    const { fen } = route.request().postDataJSON() as { fen: string };
    await releases[index].promise;
    const uci = index === 1 ? 'e7e5' : index === 0 ? 'd2d4' : 'e2e4';
    const san = index === 1 ? 'e5' : index === 0 ? 'd4' : 'e4';
    await route.fulfill({ json: { fen, lines: [{
      score_cp: index === 2 ? 55 : 111, mate: null, moves: [{ uci, san }],
    }] } });
    completed[index].resolve();
  });
  await page.goto('/analysis');
  await expect.poll(() => requests).toBe(1);
  await expect(page.getByRole('button', { name: 'Make move' })).toBeEnabled();
  await movePiece(page, page.locator('.cg-wrap'), 'e2', 'e4');
  await expect.poll(() => requests).toBe(2);
  await page.getByRole('button', { name: 'First', exact: true }).click();
  await expect.poll(() => requests).toBe(3);
  releases[2].resolve();
  await expect(page.getByLabel('Line 1 evaluation +0.55')).toBeVisible();
  releases[0].resolve(); releases[1].resolve();
  await Promise.all(completed.map(item => item.promise));
  await expect(page.getByLabel('Line 1 evaluation +0.55')).toBeVisible();
  await expect(page.getByLabel('Line 1 evaluation +1.11')).toHaveCount(0);
});
