import { test, expect } from '@playwright/test';

test.describe('Offline and Phase 2 Exit Criterion', () => {
  test('Complete journey and offline move queuing', async ({ page, context }) => {
    page.on('console', msg => console.log(msg.text()));
    await page.route('/v1/players', async route => {
      await route.fulfill({ json: { id: 'player-1', preferences: {}, access_token: 'token-1' } });
    });
    await page.route('/v1/play-sessions', async route => {
      await route.fulfill({ json: { id: 'session-1', player_id: 'player-1', status: 'active', current_fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1', pgn: '' } });
    });
    let isOffline = false;
    await page.route('/v1/play-sessions/session-1/moves', async route => {
      if (isOffline) {
        await route.abort('internetdisconnected');
      } else {
        await route.fulfill({ json: { opponent_move: 'e7e5' } });
      }
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

    // Make a move while offline using the backdoor
    await page.evaluate(async () => {
      if ((window as any).__e2e_move) {
        await (window as any).__e2e_move();
      }
    });

    // Check offline indicator or queued message
    const errorMsg = page.locator('.error');
    await expect(errorMsg).toContainText('Offline. Move queued');

    // Go back online
    isOffline = false;
    await context.setOffline(false);
    
    // Check if the moves were synced by evaluating the idb-keyval queue length
    // Actually we can just wait for the error message to clear or the sync event
    // Since we dispatch an event, we can listen for it
    await page.evaluate(() => {
      return new Promise((resolve) => {
        const handle = () => {
          window.removeEventListener('scan64-moves-synced', handle);
          resolve(true);
        };
        window.addEventListener('scan64-moves-synced', handle);
        // Also check if already synced if event fired before we added listener
        // but we just went online so it should fire.
      });
    });

    // Navigation to other Phase 2 views
    const views = ['Analysis Board', 'Opening Explorer', 'Famous Games', 'Daily Training', 'Profile'];
    for (const view of views) {
      await page.getByRole('button', { name: view }).click();
      await expect(page.locator('main')).toBeVisible();
    }
  });
});
