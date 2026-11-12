import { expect, test } from '@playwright/test';
import { mockTodayReports, prepareToday } from './today';

const gameId = '00000000-0000-0000-0000-000000000001';

test('first visit creates a named player once and opens Today', async ({ page }) => {
  await mockTodayReports(page);
  const registrations: Array<{ id: string; display_name: string }> = [];
  await page.route('**/v1/players', async route => {
    const player = route.request().postDataJSON() as { id: string; display_name: string };
    registrations.push(player);
    await route.fulfill({ json: { ...player, preferences: {}, access_token: 'named-token' } });
  });
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Welcome to Scan64' })).toBeVisible();
  expect(registrations).toHaveLength(0);
  await page.getByLabel('Display name').fill('Ada');
  await page.getByRole('button', { name: 'Start learning' }).click();
  await expect(page.getByRole('heading', { name: 'Today', exact: true })).toBeVisible();
  for (const action of ['Play', 'Train', 'Review']) {
    await expect(page.locator('main').getByRole('button', { name: new RegExp(`^${action}`) })).toBeVisible();
  }
  expect(registrations).toHaveLength(1);
  expect(registrations[0].display_name).toBe('Ada');
  await page.reload();
  await expect(page.getByRole('heading', { name: 'Today', exact: true })).toBeVisible();
  await expect(page.getByLabel('Display name')).toHaveCount(0);
  expect(registrations).toHaveLength(1);
});

test('Today links an analysed game and uses skill names rather than codes', async ({ page }) => {
  await prepareToday(page);
  await page.route('**/v1/players/*/games*', route => route.fulfill({ json: {
    items: [{ id: gameId, white: 'Ada', black: 'Bot', result: '0-1', date: '', created_at: '', diagnosis_count: 1 }], next_cursor: null,
  } }));
  await page.route('**/v1/reports/weekly?*', route => route.fulfill({ json: {
    player_id: 'player-1', games_played: 1, active_concepts_observed: 2,
    active_mastery: [
      { concept_code: 'tactics.pin', name: 'Pin', mastery: 0.8 },
      { concept_code: 'board_awareness.hanging_piece', name: 'Hanging piece', mastery: 0.2 },
    ], top_recurring_diagnosis: null,
  } }));
  await page.route(`**/v1/games/${gameId}/positions`, route => route.fulfill({ json: [] }));
  await page.route(`**/v1/games/${gameId}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  await page.goto('/');
  const skills = page.locator('.study-panel').filter({ has: page.getByRole('heading', { name: 'Skills to practise' }) });
  await expect(skills.getByRole('listitem')).toHaveText(['Hanging piece', 'Pin']);
  await expect(page.locator('main')).not.toContainText('board_awareness.hanging_piece');
  await expect(page.locator('main')).not.toContainText('tactics.pin');
  await page.getByRole('link', { name: 'Review this game' }).click();
  await expect(page).toHaveURL(`/games/${gameId}/analysis`);
  await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
});

for (const width of [1280, 390]) {
  for (const colorScheme of ['light', 'dark'] as const) {
    test(`shell navigation at ${width}px in ${colorScheme}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.emulateMedia({ colorScheme });
      await prepareToday(page);
      await page.route('**/v1/learning/session?*', route => route.fulfill({ json: { session_id: 'study-1', lessons: [] } }));
      await page.route('**/v1/players/*/progress', route => route.fulfill({ json: { player_id: 'player-1', skills: [] } }));
      await page.route('**/v1/players/*/evidence', route => route.fulfill({ json: { player_id: 'player-1', evidence_items: [] } }));
      await page.route('**/v1/coaches/*/dashboard', route => route.fulfill({ json: { coach_id: 'player-1', students: [] } }));
      await page.route('**/v1/content/famous-games', route => route.fulfill({ json: [] }));
      await page.goto('/');
      await expect(page.getByText('More analysed games are needed to spot recurring patterns.')).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      await testInfo.attach(`today-${width}-${colorScheme}`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
      const nav = page.getByRole('navigation', { name: 'Main navigation' });
      for (const [label, route, heading] of [
        ['Play', '/play', 'Play against Scan64'],
        ['Review', '/games', 'Your games'],
        ['Train', '/training', 'No training lessons available'],
        ['Profile', '/profile', 'Player Profile: player-1'],
        ['Coach', '/coach', 'Pattern ledger'],
        ['Today', '/', 'Today'],
      ]) {
        await nav.getByRole('button', { name: label, exact: true }).click();
        await expect(page).toHaveURL(route);
        await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
        await expect(nav.getByRole('button', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
      }
      for (const [label, route, heading] of [
        ['Opening Explorer', '/explorer', 'Opening Explorer'],
        ['Famous Games', '/famous', 'Study Famous Games'],
        ['Import PGN', '/import', 'Import Game'],
      ]) {
        await nav.getByRole('button', { name: 'Library', exact: true }).click();
        await page.getByRole('button', { name: label, exact: true }).click();
        await expect(page).toHaveURL(route);
        await expect(page.getByRole('heading', { name: heading, exact: true })).toBeVisible();
        await expect(nav.getByRole('button', { name: 'Library', exact: true })).toHaveAttribute('aria-current', 'page');
      }
      await nav.getByRole('button', { name: 'Review', exact: true }).click();
      await page.getByRole('button', { name: 'Analysis Board', exact: true }).click();
      await expect(page).toHaveURL('/analysis');
      await expect(page.getByRole('heading', { name: 'Analysis Board' })).toBeVisible();
      await page.goto('/not-built');
      await expect(page.getByTestId('not-found')).toHaveText('Page not found');
    });
  }
}
