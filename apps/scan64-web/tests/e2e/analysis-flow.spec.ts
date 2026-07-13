import { test, expect } from '@playwright/test';

test('analysis board play from here flow', async ({ page }) => {
  await page.goto('/');

  // Navigate to analysis board
  await page.click('button:has-text("Analysis Board")');
  await expect(page.locator('h2:has-text("Analysis Board")')).toBeVisible();

  // Load a FEN
  const fenInput = page.getByPlaceholder('Paste FEN here');
  await fenInput.fill('rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2');
  await page.click('button:has-text("Load FEN")');

  // Start play from here
  // Intercept API calls to mock them if we are not running backend
  await page.route('**/v1/games', async route => {
    await route.fulfill({ json: { id: 'test-game' } });
  });
  await page.route('**/v1/players', async route => {
    await route.fulfill({ json: { id: 'test-player' } });
  });
  await page.route('**/v1/play-sessions', async route => {
    await route.fulfill({ json: { id: 'test-session', status: 'active' } });
  });

  await page.click('button[data-testid="play-from-here"]');

  // Verify transition to play screen
  await expect(page.locator('h1:has-text("Play against Scan64")')).toBeVisible();
  
  // Verify that the session is active
  await expect(page.locator('[data-testid="session-info"]')).toContainText('Status: active');
});
