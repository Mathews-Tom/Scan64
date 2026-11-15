import { expect, test, type Page } from '@playwright/test';
import type { PuzzleRunRead } from '../../src/api/types';
import { prepareToday } from './today';

const daily = {
  utc_day: '2026-11-16', rating: 1200, rated_count: 0, provisional: true,
  overdue_count: 0, total: 10, completed: 0, remaining: 10,
  actual_mix: { due: 0, weak: 0, exploration: 10 }, message: 'Daily set reserved.',
};
const streak = { current: 0, best: 0, reset_reason: null };
const run: PuzzleRunRead = {
  run_id: 'layout-run', puzzle_id: 'layout-puzzle',
  fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1',
  setup: 'e2e4', played_moves: [], cursor: 0, revision: 0, wrong_attempts: 0,
  maximum_wrong_attempts: 3, state: 'active', hint_step: 1,
  hints: [{ step: 1, text: 'Inspect the centre. '.repeat(40), visualizations: [] }],
  mode: 'daily', theme: null, streak, daily,
  selection_reason: 'exploration', widening: '200', rated: true,
};

async function preparePuzzles(page: Page, active: boolean) {
  await prepareToday(page);
  await page.route('**/v1/puzzles/modes', route => route.fulfill({ json: { themes: ['fork'], streak } }));
  await page.route('**/v1/puzzles/active*', route => route.fulfill({ json: {
    status: active ? 'ready' : 'empty', run: active ? run : null,
    daily, streak, message: active ? 'Run resumed.' : 'Import puzzles to begin.',
  } }));
}

for (const viewport of [
  { width: 960, height: 560 }, { width: 960, height: 820 },
  { width: 1199, height: 820 }, { width: 1200, height: 820 },
  { width: 1440, height: 900 }, { width: 1920, height: 1080 },
]) {
  test(`puzzle board and controls remain bounded at ${viewport.width} × ${viewport.height}`, async ({ page }, info) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ colorScheme: viewport.width % 2 ? 'light' : 'dark' });
    await preparePuzzles(page, true);
    await page.goto('/puzzles');
    const board = page.locator('.puzzles-screen .coach-board__frame');
    const controls = page.getByRole('region', { name: 'Current puzzle', exact: true });
    await expect(board).toBeVisible();
    await expect(controls).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await expect.poll(async () => {
      const a = (await board.boundingBox())!;
      const b = (await controls.boundingBox())!;
      return Math.abs(a.y - b.y);
    }).toBeLessThanOrEqual(1);
    const a = (await board.boundingBox())!;
    const b = (await controls.boundingBox())!;
    expect(Math.abs(a.width - a.height)).toBeLessThanOrEqual(1);
    expect(a.width).toBeGreaterThanOrEqual(400);
    expect(a.width).toBeLessThanOrEqual(640);
    expect(b.width).toBeGreaterThanOrEqual(320);
    expect(b.width).toBeLessThanOrEqual(360);
    expect(Math.abs(b.x - a.x - a.width - 24)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await info.attach('workspace', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
}

test('empty puzzle setup uses the canvas and summaries do not wait for a missing board', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 820 });
  await preparePuzzles(page, false);
  await page.goto('/puzzles');
  await expect(page.getByText('No imported puzzles are available.', { exact: true })).toBeVisible();
  const controls = page.getByRole('region', { name: 'Puzzle controls', exact: true });
  const canvas = (await page.locator('.puzzles-screen').boundingBox())!;
  const setup = (await controls.boundingBox())!;
  expect(Math.abs(setup.x - canvas.x)).toBeLessThanOrEqual(1);
  expect(Math.abs(setup.width - canvas.width)).toBeLessThanOrEqual(1);
  const summary = (await page.getByRole('region', { name: 'Daily puzzle practice', exact: true }).boundingBox())!;
  expect(summary.y - setup.y - setup.height).toBeLessThanOrEqual(24);
  expect(summary.y).toBeGreaterThan(setup.y);
  await expect(page.getByRole('button', { name: 'Start daily', exact: true })).toHaveCount(0);
});

test('compact navigation expands without squeezing the workspace and dismisses with focus return', async ({ page }) => {
  await page.setViewportSize({ width: 960, height: 820 });
  await preparePuzzles(page, true);
  await page.goto('/puzzles');
  const board = page.getByTestId('puzzle-board');
  await expect(board).toBeVisible();
  const before = (await board.boundingBox())!;
  const expand = page.getByRole('button', { name: 'Expand sidebar', exact: true });
  await expand.focus();
  await page.keyboard.press('Enter');
  const collapse = page.getByRole('button', { name: 'Collapse sidebar', exact: true });
  await expect(collapse).toBeVisible();
  const nav = page.getByRole('navigation', { name: 'Main navigation' });
  await expect(nav.getByRole('button', { name: 'Puzzles', exact: true }).locator('span')).toBeVisible();
  const after = (await board.boundingBox())!;
  expect(after.width).toBe(before.width);
  await page.keyboard.press('Escape');
  await expect(expand).toBeFocused();
  await expect(page.locator('main')).not.toHaveAttribute('inert');
  await expand.click();
  await nav.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
  await expect(page.locator('main')).toBeFocused();
  await expect(page.locator('main')).not.toHaveAttribute('inert');
});

test('wide navigation preference survives reload and compact drawer closes at the breakpoint', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto('/settings');
  await page.getByRole('button', { name: 'Collapse sidebar', exact: true }).click();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Expand sidebar', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Expand sidebar', exact: true }).click();
  await page.setViewportSize({ width: 960, height: 820 });
  await page.getByRole('button', { name: 'Expand sidebar', exact: true }).click();
  await page.setViewportSize({ width: 1200, height: 820 });
  await expect(page.locator('main')).not.toHaveAttribute('inert');
  await expect(page.getByRole('button', { name: 'Collapse sidebar', exact: true })).toBeVisible();
});

for (const viewport of [{ width: 740, height: 360 }, { width: 900, height: 600 }]) {
  test(`stacked board fits the usable height at ${viewport.width} × ${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await preparePuzzles(page, true);
    await page.goto('/puzzles');
    const board = page.locator('.puzzles-screen .coach-board__frame');
    await expect(board).toBeVisible();
    const bounds = (await board.boundingBox())!;
    const clearance = viewport.width < 768 ? 128 : 0;
    expect(bounds.height).toBeLessThanOrEqual(viewport.height - clearance - 32);
    expect(Math.abs(bounds.width - bounds.height)).toBeLessThanOrEqual(1);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    if (viewport.width < 768) await expect(page.locator('.app-brand-name')).toBeVisible();
  });
}
