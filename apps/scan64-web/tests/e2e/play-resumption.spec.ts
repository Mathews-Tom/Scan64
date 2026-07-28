import { expect, test } from '@playwright/test';

test('resumes an active game after reload and browser navigation', async ({ page }) => {
  let resumedSessionReads = 0;
  let moveRequests = 0;

  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'token-1' } });
  });
  await page.route('**/v1/play-sessions', async route => {
    await route.fulfill({
      json: { id: 'session-1', player_id: 'player-1', opponent_config: {}, status: 'active' },
    });
  });
  await page.route('**/v1/play-sessions/session-1', async route => {
    resumedSessionReads += 1;
    await route.fulfill({
      json: {
        id: 'session-1',
        player_id: 'player-1',
        game_id: 'game-1',
        opponent_config: {},
        status: 'active',
      },
    });
  });
  await page.route('**/v1/games/game-1', async route => {
    await route.fulfill({
      json: {
        id: 'game-1',
        pgn: moveRequests >= 2 ? '1. e4 e5 2. Nf3 Nc6 *' : '1. e4 e5 *',
        white: 'player-1',
        black: 'Stockfish',
        result: '*',
      },
    });
  });
  await page.route('**/v1/play-sessions/session-1/moves', async route => {
    moveRequests += 1;
    await route.fulfill({
      json: {
        opponent_move: moveRequests === 1 ? 'e7e5' : 'b8c6',
        status: 'active',
      },
    });
  });

  await page.goto('/');
  await page.getByRole('button', { name: 'Play Game' }).click();
  await page.getByTestId('start-btn').click();
  await expect(page.getByTestId('session-info')).toContainText('Status: active');

  await page.evaluate(async () => {
    const candidate: unknown = window;
    if (candidate === null || typeof candidate !== 'object' || !('__e2e_move' in candidate)) {
      throw new Error('Expected development move hook');
    }
    const move = candidate.__e2e_move;
    if (typeof move !== 'function') throw new Error('Expected callable development move hook');
    await move();
  });

  const board = page.getByTestId('chessground-board');
  const hasResumedPosition = async () =>
    await board.locator('piece').evaluateAll((pieces) => {
      const keys = pieces.map((piece) => (piece as HTMLElement & { cgKey?: string }).cgKey);
      return keys.includes('e4') && keys.includes('e5');
    });
  await expect.poll(hasResumedPosition).toBe(true);

  await page.reload();
  await expect.poll(() => resumedSessionReads).toBeGreaterThanOrEqual(1);
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
  await expect.poll(hasResumedPosition).toBe(true);

  await page.evaluate(async () => {
    const candidate: unknown = window;
    if (candidate === null || typeof candidate !== 'object' || !('__e2e_move' in candidate)) {
      throw new Error('Expected development move hook');
    }
    const move = candidate.__e2e_move;
    if (typeof move !== 'function') throw new Error('Expected callable development move hook');
    await move('g1', 'f3');
  });
  await expect.poll(async () =>
    await board.locator('piece').evaluateAll((pieces) => {
      const keys = pieces.map((piece) => (piece as HTMLElement & { cgKey?: string }).cgKey);
      return keys.includes('f3') && keys.includes('c6');
    }),
  ).toBe(true);

  resumedSessionReads = 0;
  await page.getByRole('button', { name: 'Home' }).click();
  await page.goBack();
  await expect.poll(() => resumedSessionReads).toBeGreaterThanOrEqual(1);
  await expect.poll(async () =>
    await board.locator('piece').evaluateAll((pieces) => {
      const keys = pieces.map((piece) => (piece as HTMLElement & { cgKey?: string }).cgKey);
      return keys.includes('f3') && keys.includes('c6');
    }),
  ).toBe(true);
});
