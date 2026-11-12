import { expect, test, type Locator, type Page } from '@playwright/test';
import { prepareToday } from './today';
import { makeGameReview } from '../../src/components/review.test-fixtures';

test.use({ hasTouch: true });

async function tabTo(page: Page, target: Locator) {
  for (let index = 0; index < 100; index++) {
    if (await target.evaluate(element => element === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`Keyboard could not reach ${await target.getAttribute('aria-label') ?? await target.textContent()}`);
}

async function enter(page: Page, target: Locator, text: string) {
  await tabTo(page, target);
  await page.keyboard.press('ControlOrMeta+A');
  await page.keyboard.insertText(text);
  await page.keyboard.press('Enter');
}

async function reflow(page: Page) {
  const dimensions = await page.evaluate(() => {
    const main = document.querySelector('main')!;
    return { document: [document.documentElement.scrollWidth, document.documentElement.clientWidth],
      main: [main.scrollWidth, main.clientWidth] };
  });
  expect(dimensions.document[0]).toBeLessThanOrEqual(dimensions.document[1]);
  expect(dimensions.main[0]).toBeLessThanOrEqual(dimensions.main[1]);
}

test('keyboard-only analysis entry, errors, all promotions and direct board position', async ({ page }, testInfo) => {
  await prepareToday(page);
  await page.goto('/analysis');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('main')).toBeFocused();
  const input = page.getByRole('textbox', { name: 'Move (UCI or SAN)' });
  await enter(page, input, 'e2e5');
  await expect(input).toBeFocused();
  await expect(page.getByRole('alert')).toContainText('Enter a legal move');
  await enter(page, input, 'e4');
  await expect(page.locator('.coach-board').getByRole('status')).toContainText('Black to move.');
  await tabTo(page, page.getByText('Read board position', { exact: true }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('list', { name: 'Pieces by square' })).toContainText('e4: White pawn');
  const fen = page.getByPlaceholder('Paste FEN here');
  for (const piece of ['queen', 'rook', 'bishop', 'knight']) {
    await tabTo(page, fen);
    await page.keyboard.press('ControlOrMeta+A');
    await page.keyboard.insertText('8/4P3/8/8/8/8/8/k6K w - - 0 1');
    await tabTo(page, page.getByRole('button', { name: 'Load FEN' }));
    await page.keyboard.press('Enter');
    await tabTo(page, input);
    await page.keyboard.insertText('e7e8');
    await tabTo(page, page.getByRole('button', { name: 'Make move' }));
    await page.keyboard.press('Enter');
    await expect(page.getByRole('button', { name: 'Promote to queen' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(input).toBeFocused();
    await page.keyboard.press('Enter');
    await tabTo(page, page.getByRole('button', { name: `Promote to ${piece}` }));
    await page.keyboard.press('Enter');
    await expect(page.getByRole('group', { name: 'Choose promotion piece' })).toHaveCount(0);
    await expect(input).toBeFocused();
  }
  await testInfo.attach('keyboard-analysis-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
  await testInfo.attach('keyboard-analysis', { body: await page.screenshot(), contentType: 'image/png' });
});

const routes = ['/', '/play', '/games', '/analysis', '/training', '/puzzles', '/profile', '/coach', '/library', '/import', '/famous', '/explorer', '/not-found'];
for (const size of [{ width: 320, height: 640 }, { width: 640, height: 320 }]) {
  test.describe(`touch reflow ${size.width}x${size.height}`, () => {
    test.use({ viewport: size, hasTouch: true, reducedMotion: 'reduce' });
    for (const path of routes) test(`${path} has named landmarks and document/main reflow`, async ({ page }, testInfo) => {
      // Exercise both normal and server-error/empty content without depending on a running API.
      await page.route('**/v1/**', route => route.fulfill({ status: 404, json: { detail: 'Not found' } }));
      await prepareToday(page);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await page.goto(path);
      await expect(page.getByRole('main')).toBeVisible();
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
      await reflow(page);
      expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
      await expect(page.locator('button:not([aria-label])').filter({ hasText: /^\s*$/ })).toHaveCount(0);
      for (const board of await page.locator('.cg-wrap').all()) {
        expect((await board.boundingBox())!.width).toBeLessThanOrEqual(size.width === 320 ? 320 : 400);
      }
      await testInfo.attach('role-name-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
      await testInfo.attach('reflow', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
    });
  });
}

test('keyboard review selection is read-only and maintains 320px reflow', async ({ page }, testInfo) => {
  await prepareToday(page);
  const id = '00000000-0000-0000-0000-000000000001';
  await page.route(`**/v1/games/${id}/review`, route => route.fulfill({ json: makeGameReview({ game_id: id }) }));
  await page.route(`**/v1/games/${id}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  await page.setViewportSize({ width: 320, height: 640 });
  await page.goto(`/games/${id}/analysis`);
  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Move (UCI or SAN)' })).toHaveCount(0);
  const initial = page.getByRole('button', { name: 'Initial position', exact: true }).first();
  await tabTo(page, initial);
  await page.keyboard.press('Enter');
  await expect(initial).toHaveAttribute('aria-current', 'step');
  await reflow(page);
  await testInfo.attach('review-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});

for (const size of [{ width: 320, height: 640 }, { width: 640, height: 320 }])
test(`Black puzzle local validation, hint and solve at ${size.width}x${size.height}`, async ({ page }, testInfo) => {
  await prepareToday(page);
  let calls = 0;
  let run = { run_id: 'keyboard-run', puzzle_id: 'keyboard-puzzle',
    fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', setup: 'e2e4',
    played_moves: [] as string[], cursor: 0, revision: 0, wrong_attempts: 0, maximum_wrong_attempts: 3,
    state: 'active', hint_step: 0, hints: [] as { step: number; text: string; visualizations: never[] }[] };
  const streak = { current: 0, best: 0, reset_reason: null };
  const daily = { utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
    overdue_count: 0, total: 1, completed: 0, remaining: 1,
    actual_mix: { due: 0, weak: 0, exploration: 1 }, message: 'Daily set reserved.' };
  run = { ...run, ...{ mode: 'daily', theme: null, streak, daily,
    selection_reason: 'exploration', widening: '200', rated: true } };
  await page.route('**/v1/puzzles/modes', route => route.fulfill({ json: { themes: ['fork'], streak } }));
  await page.route('**/v1/puzzles/{runs,active}**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/v1/puzzles/active') return route.fulfill({ json: { status: 'ready', run, daily, streak, message: '' } });
    if (path.endsWith('/hints')) run = { ...run, hint_step: 1, revision: 1, hints: [{ step: 1, text: 'Inspect the central pawns.', visualizations: [] }] };
    if (path.endsWith('/moves')) {
      calls++;
      expect(route.request().postDataJSON()).toEqual({ expected_cursor: 0, expected_revision: 1, move: 'e7e5' });
      run = { ...run, fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2', state: 'solved', cursor: 1, revision: 2, played_moves: ['e7e5'] };
    }
    await route.fulfill({ json: run });
  });
  await page.setViewportSize(size);
  await page.goto('/puzzles');
  const input = page.getByRole('textbox', { name: 'Move (UCI or SAN)' });
  await expect(input).toBeVisible();
  await expect(page.getByTestId('puzzle-board')).toHaveClass(/orientation-black/);
  await enter(page, input, 'e7e4');
  await expect(input).toBeFocused();
  expect(calls).toBe(0);
  await tabTo(page, page.getByRole('button', { name: 'Show hint', exact: true }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('Inspect the central pawns.', { exact: true })).toBeVisible();
  if (size.width === 320) await enter(page, input, 'e5');
  else {
    await page.getByTestId('puzzle-board').scrollIntoViewIfNeeded();
    const box = (await page.getByTestId('puzzle-board').boundingBox())!;
    await page.touchscreen.tap(box.x + box.width * 3.5 / 8, box.y + box.height * 6.5 / 8);
    await page.touchscreen.tap(box.x + box.width * 3.5 / 8, box.y + box.height * 4.5 / 8);
  }
  await expect(page.getByText(/solved/i)).toBeVisible();
  expect(calls).toBe(1);
  await reflow(page);
  await testInfo.attach('puzzle-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
  await testInfo.attach('black-puzzle', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
});

test('keyboard starts a game, receives opponent reply and opens/closes a hint', async ({ page }, testInfo) => {
  await prepareToday(page);
  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
  });
  await page.route('**/v1/play-sessions', route => route.fulfill({ json: {
    id: 'keyboard-play', player_id: 'player-1', opponent_config: {}, status: 'active',
  } }));
  await page.route('**/v1/play-sessions/keyboard-play/moves', async route => {
    expect(route.request().postDataJSON().move).toBe('e2e4');
    await route.fulfill({ json: { opponent_move: 'e7e5', status: 'active' } });
  });
  await page.route('**/v1/insights/position', async route => {
    expect(route.request().postDataJSON()).toMatchObject({ play_session_id: 'keyboard-play', step: 1 });
    await route.fulfill({ json: { step: 1, text: 'Develop a piece toward the centre.', visualizations: [] } });
  });
  await page.goto('/play');
  await tabTo(page, page.getByTestId('start-btn'));
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
  const input = page.getByRole('textbox', { name: 'Move (UCI or SAN)' });
  await enter(page, input, 'e4');
  await expect(page.getByTestId('chessground-board-san-move-list')).toContainText('e5');
  await tabTo(page, page.getByRole('button', { name: 'Hint', exact: true }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('Develop a piece toward the centre.', { exact: true })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button', { name: 'Hint', exact: true })).toBeFocused();
  await expect(page.getByRole('button', { name: 'Close hints' })).toHaveCount(0);
  await testInfo.attach('play-hint-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});

test.describe('touch move entry without dragging', () => {
  test.use({ hasTouch: true, viewport: { width: 640, height: 320 } });
  test('taps source and destination on the White analysis board', async ({ page }, testInfo) => {
    await prepareToday(page);
    await page.goto('/analysis');
    const board = page.getByTestId('free-analysis-board');
    await board.scrollIntoViewIfNeeded();
    const box = (await board.boundingBox())!;
    await page.touchscreen.tap(box.x + box.width * 4.5 / 8, box.y + box.height * 6.5 / 8);
    await page.touchscreen.tap(box.x + box.width * 4.5 / 8, box.y + box.height * 4.5 / 8);
    await expect(page.getByPlaceholder('Paste FEN here')).toHaveValue(/4P3/);
    await reflow(page);
    await testInfo.attach('white-touch-landscape', { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
  });
});

test('keyboard Famous Game prediction converts UCI to canonical SAN without moving its board', async ({ page }, testInfo) => {
  await prepareToday(page);
  let attempts = 0;
  await page.route('**/v1/content/famous-games', route => route.fulfill({ json: [{
    id: 'opera', skill_mapping: { 'tactics.development': 1 },
    payload: { title: 'Opera Game', historical_context: 'Paris, 1858.', strategic_context: 'Develop pieces.',
      moves: ['e4'], decisions: [{ id: 'opening', ply: 0,
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
        prompt: 'Choose a move.', played_move: 'e4', accepted_moves: ['e4'],
        verified_alternatives: [], hints: [], comparison: 'e4 controls the centre.' }] },
  }] }));
  await page.route('**/v1/content/famous-games/opera/attempts', async route => {
    attempts++;
    expect(route.request().postDataJSON().response_payload.move).toBe('e4');
    await route.fulfill({ json: { id: 'attempt-1', success: true, hint_assisted: false } });
  });
  await page.goto('/famous');
  const opera = page.getByRole('button', { name: 'Opera Game' });
  await expect(opera).toBeVisible();
  await tabTo(page, opera);
  await page.keyboard.press('Enter');
  await expect(page.getByRole('textbox', { name: 'Move (UCI or SAN)' })).toHaveCount(0);
  const prediction = page.getByLabel('Your move');
  await enter(page, prediction, 'e2e5');
  await expect(prediction).toBeFocused();
  expect(attempts).toBe(0);
  await enter(page, prediction, 'e2e4');
  await expect(page.getByText('Correct.', { exact: true })).toBeVisible();
  expect(attempts).toBe(1);
  await testInfo.attach('famous-san-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});

test('keyboard Opening Explorer shares legal entry and readable current position', async ({ page }, testInfo) => {
  await prepareToday(page);
  await page.route('**/v1/learning/session?*', route => route.fulfill({ json: { session_id: 'opening-session', lessons: [] } }));
  await page.goto('/explorer');
  const input = page.getByRole('textbox', { name: 'Move (UCI or SAN)' });
  await enter(page, input, 'e4');
  await expect(page.locator('.coach-board').getByRole('status')).toContainText('Black to move.');
  await tabTo(page, page.getByText('Read board position', { exact: true }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('list', { name: 'Pieces by square' })).toContainText('e4: White pawn');
  await testInfo.attach('explorer-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});
