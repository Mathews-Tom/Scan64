import { test, expect } from '@playwright/test';
import { movePiece } from './board';

test.describe('Review Interaction Sequencing', () => {
  test('does not render a client-generated interruption before M45', async ({ page }) => {
    page.on('console', msg => console.log('BROWSER:', msg.text()));
    
    // Intercept API calls
    await page.route('**/v1/players', async route => {
      const { id } = route.request().postDataJSON() as { id: string };
      await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
    });


    await page.route('**/v1/learning/session?*', async route => {
      await route.fulfill({ json: { session_id: 'study-1', lessons: [] } });
    });
    await page.route('**/v1/play-sessions', async route => {
      await route.fulfill({ json: { id: 'session-1', player_id: 'player-1', status: 'active' } });
    });

    await page.route('**/v1/play-sessions/*/moves', async route => {
      await route.fulfill({
        json: {
          opponent_move: 'e7e5',
          status: 'active',
        },
      });
    });

    await page.goto('/');
    await page.getByText('Play Game').click();
    await page.getByTestId('coach-mode-toggle').check();
    await page.getByTestId('start-btn').click();
    await expect(page.getByTestId('session-info')).toBeVisible();

    await movePiece(page, page.getByTestId('chessground-board'), 'e2', 'e4');

    await expect(page.locator('.error')).not.toBeVisible();
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();
  });
});
