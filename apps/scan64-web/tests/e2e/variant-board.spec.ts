import { test, expect } from '@playwright/test';
import { prepareToday } from './today';
import { readFileSync } from 'node:fs';

// Written by the server's own variant_position() (scan64.api.variants); see VariantAnalysisBoard.test.tsx.
const positions = JSON.parse(readFileSync(
  new URL('../../src/components/variantPositions.test-fixtures.json', import.meta.url), 'utf8',
)) as Record<string, Record<string, unknown>>;
const crazyhouse = positions.crazyhouse;

test.beforeEach(async ({ page }) => {
  await prepareToday(page);
  await page.addInitScript(() => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'test-token');
  });
  await page.route('**/v1/analysis/position', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    await route.fulfill({ json: { fen, variant: 'crazyhouse', lines: [] } });
  });
});

test('a pointer move while the next variant position loads is not played', async ({ page }) => {
  const requested: string[][] = [];
  let release: () => void = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/v1/variants/position', async route => {
    const { moves } = route.request().postDataJSON() as { moves: string[] };
    requested.push(moves);
    if (moves.join(' ') === 'e2e4') await held;
    const read = crazyhouse[moves.join(' ')];
    if (read === undefined) await route.fulfill({ status: 422, json: { detail: `No fixture for ${moves.join(' ')}` } });
    else await route.fulfill({ json: read });
  });

  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Review', exact: true }).click();
  await page.getByRole('button', { name: 'Analysis Board' }).click();
  await page.getByLabel('Variant', { exact: true }).selectOption('crazyhouse');
  await page.getByRole('button', { name: 'Open Crazyhouse board' }).click();
  // The button disables itself; focus goes to the opened board's heading.
  await expect(page.getByRole('heading', { name: 'Crazyhouse board' })).toBeFocused();
  const status = page.getByTestId('variant-position-status');
  await expect(status).toHaveText('White to move.');

  const field = page.getByLabel('Move (UCI)', { exact: true });
  await field.fill('e2e4');
  await field.press('Enter');
  await expect.poll(() => requested).toContainEqual(['e2e4']);
  await expect(status).toHaveText('Loading position…');

  const board = page.getByTestId('variant-analysis-board');
  await board.scrollIntoViewIfNeeded();
  const bounds = (await board.boundingBox())!;
  const square = async (name: string) => {
    const file = name.charCodeAt(0) - 'a'.charCodeAt(0);
    const rank = Number(name[1]);
    await page.mouse.click(bounds.x + (file + 0.5) * bounds.width / 8, bounds.y + (8 - rank + 0.5) * bounds.height / 8);
  };
  // d2-d4 was legal before e2e4; it is not White's move now, and the board must not take it.
  await square('d2');
  await square('d4');
  release();
  await expect(status).toHaveText('Black to move.');
  expect(requested).not.toContainEqual(['e2e4', 'd2d4']);
  await expect(page.getByRole('list', { name: 'Move list' }).getByRole('listitem')).toHaveCount(1);
  await expect(page.getByRole('alert')).toHaveCount(0);
});
