import { test, expect } from '@playwright/test';

interface E2EWindow extends Window {
  __e2e_move?: () => Promise<void>;
}

test.describe('Offline and Phase 2 Exit Criterion', () => {
  test('Complete journey and offline move queuing', async ({ page, context }) => {
    let isOffline = false;
    await page.route('/v1/players', async route => {
      await route.fulfill({ json: { id: 'player-1', preferences: {}, access_token: 'token-1' } });
    });
    await page.route('/v1/play-sessions', async route => {
      await route.fulfill({ json: { id: 'session-1', player_id: 'player-1', status: 'active', current_fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', pgn: '' } });
    });
    let syncShouldFail = true;
    let onlineMoveRequests = 0;
    await page.route('/v1/play-sessions/session-1/moves', async route => {
      if (isOffline) {
        await route.abort('internetdisconnected');
        return;
      }

      onlineMoveRequests += 1;
      if (syncShouldFail) {
        await route.fulfill({ status: 503, body: 'Synchronization unavailable' });
        return;
      }

      await route.fulfill({ json: { opponent_move: 'e7e5' } });
    });
    await page.goto('/');

    const playLink = page.getByRole('button', { name: 'Play Game' });
    await playLink.click();
    await page.getByTestId('player-id-input').fill('e2e-offline-player');
    await page.getByTestId('start-btn').click();

    // Verify session started
    await expect(page.getByTestId('session-info')).toBeVisible();
    // Wait for board to be visible
    const board = page.getByTestId('chessground-board');
    await expect(board).toBeVisible();

    // Go offline
    isOffline = true;
    await context.setOffline(true);

    // Make a move while offline using the same handler as Chessground.
    await page.evaluate(async () => {
      const testWindow = window as E2EWindow;
      if (!testWindow.__e2e_move) {
        throw new Error('E2E move hook is unavailable');
      }
      await testWindow.__e2e_move();
    });

    // Check offline indicator or queued message
    const errorMsg = page.locator('.error');
    await expect(errorMsg).toContainText('Offline. Move queued');

    // Go back online
    isOffline = false;
    await context.setOffline(false);
    
    // The first reconnect makes one failed request, retains the queued move, and reports it.
    await expect.poll(() => onlineMoveRequests).toBe(1);
    await expect(errorMsg).toContainText('Queued move could not be synchronized');

    // A later reconnect retries the retained move once and clears the visible failure.
    syncShouldFail = false;
    await context.setOffline(true);
    await context.setOffline(false);
    await expect.poll(() => onlineMoveRequests).toBe(2);
    await expect(errorMsg).toHaveCount(0);

    // Navigation to other Phase 2 views
    const views = ['Analysis Board', 'Opening Explorer', 'Famous Games', 'Daily Training', 'Profile'];
    for (const view of views) {
      await page.getByRole('button', { name: view }).click();
      await expect(page.locator('main')).toBeVisible();
    }
  });
});
