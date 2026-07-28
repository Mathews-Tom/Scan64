import { test, expect } from '@playwright/test';

test.describe('Review Interaction Sequencing', () => {
  test('does not render a client-generated interruption before M45', async ({ page }) => {
    page.on('console', msg => console.log('BROWSER:', msg.text()));
    
    // Intercept API calls
    await page.route('**/v1/players', async route => {
      await route.fulfill({ json: { id: 'player-1', preferences: {}, access_token: 'test-token' } });
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

    await page.evaluate(async () => {
      const candidate: unknown = window;
      if (candidate === null || typeof candidate !== 'object' || !('__e2e_move' in candidate)) {
        throw new Error('Expected development move hook');
      }
      const move = candidate.__e2e_move;
      if (typeof move !== 'function') throw new Error('Expected callable development move hook');
      await move();
    });

    await expect(page.locator('.error')).not.toBeVisible();
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();
  });
});
