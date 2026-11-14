import { expect, test } from '@playwright/test';

for (const width of [1440, 1024, 768, 390]) {
  for (const appearance of ['light', 'dark'] as const) {
    test(`identity shell at ${width} in ${appearance}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(value => {
        localStorage.clear();
        localStorage.setItem('scan64-appearance', value);
      }, appearance);
      await page.route('**/v1/app/info', route => route.fulfill({ json: { distribution: 'source' } }));
      const externalRequests: string[] = [];
      page.on('request', request => {
        if (new URL(request.url()).hostname !== 'localhost') externalRequests.push(request.url());
      });
      await page.goto('/settings');
      await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
      await expect(page.locator('html')).toHaveAttribute('data-appearance', appearance);
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Settings', exact: true })).toHaveAttribute('aria-current', 'page');
      await page.evaluate(() => document.fonts.ready);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      for (const button of await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button').all()) {
        const bounds = await button.boundingBox();
        expect(bounds!.height).toBeGreaterThanOrEqual(44);
        expect(bounds!.width).toBeGreaterThanOrEqual(44);
      }
      await page.keyboard.press('Tab');
      await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
      expect(externalRequests).toEqual([]);
      await page.getByRole('button', { name: 'Today', exact: true }).click();
      await expect(page.getByRole('heading', { name: 'Welcome to Scan64', exact: true })).toBeVisible();
    });
  }
}

for (const viewport of [{ width: 844, height: 390 }, { width: 1280, height: 480 }]) {
  test(`navigation remains reachable at ${viewport.width} × ${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => localStorage.clear());
    await page.route('**/v1/app/info', route => route.fulfill({ json: { distribution: 'source' } }));
    await page.goto('/');
    const headerBounds = await page.getByRole('banner').boundingBox();
    const todayBounds = await page.getByRole('button', { name: 'Today', exact: true }).boundingBox();
    expect(todayBounds!.y).toBeGreaterThanOrEqual(headerBounds!.y + headerBounds!.height);
    const settings = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Settings', exact: true });
    await settings.focus();
    await expect(settings).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Today', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Welcome to Scan64', exact: true })).toBeVisible();
  });
}
