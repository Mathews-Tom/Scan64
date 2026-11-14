import { chromium, expect } from '@playwright/test';
import { mkdir, writeFile } from 'node:fs/promises';
import path from 'node:path';

// Run against scripts/run.sh on a disposable database seeded with the documented
// legal opening-line puzzle fixture. No API interception or test-only move hooks.
const origin = process.env.SCAN64_EVIDENCE_ORIGIN ?? 'http://127.0.0.1:5173';
const directory = path.resolve(process.env.SCAN64_EVIDENCE_DIR ?? 'artifacts/m81');
await mkdir(directory, { recursive: true });
const browser = await chromium.launch({ headless: true });
const observations = [];
try {
  for (const width of [1440, 1024, 768, 390]) {
    for (const appearance of ['light', 'dark']) {
      const context = await browser.newContext({ viewport: { width, height: 1000 }, colorScheme: appearance, reducedMotion: 'reduce' });
      const page = await context.newPage();
      page.setDefaultTimeout(60000);
      const foreignHosts = new Set();
      page.on('request', request => {
        if (new URL(request.url()).host !== new URL(origin).host) foreignHosts.add(new URL(request.url()).host);
      });
      const state = { width, appearance, screens: [], foreignHosts: [] };
      async function capture(name) {
        await page.evaluate(() => document.fonts.ready);
        const layout = await page.evaluate(() => {
          const main = document.querySelector('main');
          return { viewport: innerWidth, document: document.documentElement.scrollWidth, main: main.scrollWidth, available: main.clientWidth };
        });
        expect(layout.document, `${name}: document width`).toBeLessThanOrEqual(layout.viewport);
        expect(layout.main, `${name}: main width`).toBeLessThanOrEqual(layout.available);
        await page.screenshot({ path: path.join(directory, `${name}-${width}-${appearance}.png`), fullPage: true });
        state.screens.push({ name, layout });
      }
      await page.goto(origin);
      await page.getByLabel('Display name', { exact: true }).fill(`M81 ${width} ${appearance}`);
      await page.getByRole('button', { name: 'Start learning', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Today', exact: true })).toBeVisible();
      state.playerCreated = true;
      await capture('today');
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Settings', exact: true }).click();
      await page.getByRole('radio', { name: appearance === 'light' ? 'Light' : 'Dark', exact: true }).check();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      await page.reload();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      state.appearancePersisted = true;
      await capture('settings');
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Play', exact: true }).click();
      await page.getByTestId('coach-mode-toggle').uncheck();
      await expect(page.getByTestId('start-btn')).toBeEnabled();
      await page.getByTestId('start-btn').click();
      await expect(page.getByTestId('session-info')).toContainText('Status: active');
      const move = page.getByRole('textbox', { name: 'Move (UCI or SAN)', exact: true });
      await expect(move).toBeEnabled();
      await move.fill('e2e4');
      await page.getByRole('button', { name: 'Make move', exact: true }).click();
      await expect(page.getByTestId('chessground-board-san-move-list')).toContainText('e4');
      await expect(page.getByRole('button', { name: 'Hint', exact: true })).toBeEnabled();
      await page.getByRole('button', { name: 'Hint', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Close hints', exact: true })).toBeVisible();
      state.botReplyAndHint = true;
      await capture('play');
      await page.getByRole('button', { name: 'Close hints', exact: true }).click();
      await page.getByRole('button', { name: 'Resign', exact: true }).click();
      await expect(page.getByTestId('game-result')).toBeVisible();
      await page.getByRole('link', { name: 'Review game', exact: true }).click();
      await expect(page.getByTestId('start-analysis')).toBeVisible();
      await page.getByTestId('start-analysis').click();
      await expect(async () => {
        if (await page.getByRole('button', { name: 'Refresh review', exact: true }).isEnabled()) await page.getByRole('button', { name: 'Refresh review', exact: true }).click();
        await expect(page.getByTestId('review-board')).toBeVisible();
      }).toPass({ timeout: 120000, intervals: [1000, 2000, 4000] });
      await page.getByRole('button', { name: 'Last', exact: true }).click();
      state.reviewedGame = true;
      await capture('review');
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Puzzles', exact: true }).click();
      await page.getByRole('button', { name: 'Start daily', exact: true }).click();
      await expect(page.getByTestId('puzzle-board')).toBeVisible();
      await capture('puzzles');
      const puzzleMove = page.getByRole('textbox', { name: 'Move (UCI or SAN)', exact: true });
      for (const continuation of ['e7e5', 'b8c6']) {
        await expect(puzzleMove).toBeEnabled();
        await puzzleMove.fill(continuation);
        await page.getByRole('button', { name: 'Make move', exact: true }).click();
        await expect(puzzleMove).toHaveValue('');
      }
      await expect(page.getByRole('status', { name: 'Puzzle progress', exact: true })).toContainText('Puzzle solved');
      state.puzzleSolved = true;
      await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Profile', exact: true }).click();
      await expect(page.getByTestId('profile-screen')).toBeVisible();
      await capture('profile');
      state.foreignHosts = [...foreignHosts];
      expect(state.foreignHosts).toEqual([]);
      observations.push(state);
      await writeFile(path.join(directory, 'observations.json'), JSON.stringify(observations, null, 2));
      await context.close();
    }
  }
} finally {
  await browser.close();
}
console.log(JSON.stringify({ combinations: observations.length, screenshots: observations.reduce((sum, row) => sum + row.screens.length, 0), directory }));
