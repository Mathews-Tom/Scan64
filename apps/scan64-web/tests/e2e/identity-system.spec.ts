import { expect, test } from '@playwright/test';

for (const width of [1440, 1024, 768, 390]) {
  for (const appearance of ['light', 'dark'] as const) {
    test(`identity shell at ${width} in ${appearance}`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript(value => {
        localStorage.clear();
        localStorage.setItem('scan64-appearance', value);
      }, appearance);
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
      expect(await page.evaluate(async () => Promise.all(['--sans', '--mono'].map(async token => {
        const family = getComputedStyle(document.documentElement).getPropertyValue(token).split(',')[0].trim();
        const faces = await document.fonts.load(`16px ${family}`);
        return faces.length > 0 && faces.every(face => face.status === 'loaded');
      })))).toEqual([true, true]);
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

for (const viewport of [{ width: 844, height: 390 }, { width: 1280, height: 480 }, { width: 390, height: 640 }]) {
  test(`navigation remains reachable at ${viewport.width} × ${viewport.height}`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.addInitScript(() => localStorage.clear());
    await page.goto('/');
    const headerBounds = await page.locator('.app-header').boundingBox();
    const today = page.getByRole('button', { name: 'Today', exact: true });
    await today.focus();
    const todayBounds = await today.boundingBox();
    expect(todayBounds!.y).toBeGreaterThanOrEqual(headerBounds!.y + headerBounds!.height);
    const navBounds = await page.getByRole('navigation', { name: 'Main navigation' }).boundingBox();
    const focusOutset = await today.evaluate(element => {
      const style = getComputedStyle(element);
      return Number.parseFloat(style.outlineWidth) + Number.parseFloat(style.outlineOffset);
    });
    expect(focusOutset).toBeGreaterThan(0);
    expect(todayBounds!.y - navBounds!.y).toBeGreaterThanOrEqual(focusOutset);
    expect(todayBounds!.x - navBounds!.x).toBeGreaterThanOrEqual(focusOutset);
    expect(navBounds!.x + navBounds!.width - todayBounds!.x - todayBounds!.width).toBeGreaterThanOrEqual(focusOutset);
    const settings = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Settings', exact: true });
    await settings.focus();
    await expect(settings).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Today', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Welcome to Scan64', exact: true })).toBeVisible();
  });
}

test('System appearance follows the device until explicitly overridden', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/settings');
  await expect(page.getByRole('radio', { name: 'System', exact: true })).toBeChecked();
  expect(await page.locator('html').evaluate(element => getComputedStyle(element).colorScheme)).toBe('dark');
  await page.emulateMedia({ colorScheme: 'light' });
  expect(await page.locator('html').evaluate(element => getComputedStyle(element).colorScheme)).toBe('light');
  await page.getByRole('radio', { name: 'Dark', exact: true }).check();
  expect(await page.locator('html').evaluate(element => getComputedStyle(element).colorScheme)).toBe('dark');
  await page.reload();
  await expect(page.getByRole('radio', { name: 'Dark', exact: true })).toBeChecked();
});
