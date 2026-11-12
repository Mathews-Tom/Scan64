import { expect, test, type Locator, type Page } from '@playwright/test';
import type { DeclaredLevel, PlayerOnboardingRead } from '../../src/api/types';
import { onboardingFixture } from '../../src/components/strength.test-fixtures';
import { mockTodayReports, prepareToday } from './today';

const suggestions: Record<DeclaredLevel, number> = { beginner: 2, developing: 3, club: 5, advanced: 6, not_sure: 1 };
function isLevel(value: unknown): value is DeclaredLevel {
  return value === 'beginner' || value === 'developing' || value === 'club' || value === 'advanced' || value === 'not_sure';
}

async function tabTo(page: Page, target: Locator) {
  for (let count = 0; count < 100; count++) {
    if (await target.evaluate(element => element === document.activeElement)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error('Could not reach onboarding control by keyboard');
}

async function expectReflow(page: Page) {
  const widths = await page.evaluate(() => {
    const main = document.querySelector('main')!;
    return [document.documentElement.scrollWidth - document.documentElement.clientWidth, main.scrollWidth - main.clientWidth];
  });
  expect(widths[0]).toBeLessThanOrEqual(0);
  expect(widths[1]).toBeLessThanOrEqual(0);
}

async function mockOnboarding(page: Page, initial: PlayerOnboardingRead) {
  let record = { ...initial };
  await page.route('**/v1/players/*/onboarding', async route => {
    if (route.request().method() === 'POST') {
      const body: unknown = route.request().postDataJSON();
      if (!body || typeof body !== 'object' || !('action' in body)) throw new Error('Invalid test onboarding request');
      if (body.action === 'declare') {
        if (!('declared_level' in body) || !isLevel(body.declared_level)) throw new Error('Invalid test declaration');
        record = { ...record, status: 'in_progress', declared_level: body.declared_level,
          suggested_bot_level: suggestions[body.declared_level], mapping_version: 'declared-level-v1',
          intro_started_at: record.intro_started_at ?? '2026-11-13T10:00:00Z' };
      } else if (body.action === 'skip') record = { ...record, status: 'skipped', skipped_at: '2026-11-13T11:00:00Z' };
    }
    await route.fulfill({ json: record });
  });
  await page.route('**/v1/players/*/bot-choice', async route => {
    const body: unknown = route.request().postDataJSON();
    if (!body || typeof body !== 'object' || !('bot_level' in body) || typeof body.bot_level !== 'number') throw new Error('Invalid test manual choice');
    record = { ...record, manual_bot_level: body.bot_level, preferred_bot_level: body.bot_level };
    await route.fulfill({ json: record });
  });
  await page.route('**/v1/bots', route => route.fulfill({ json: [1, 2, 8].map(level => ({ level, name: `Bot ${level}`, description: 'Relative bot level, not Elo' })) }));
  return {
    get: () => record,
    replace: (next: PlayerOnboardingRead) => { record = next; },
  };
}

test.use({ viewport: { width: 320, height: 640 }, reducedMotion: 'reduce' });

test('new registration, native keyboard declaration, reload and manual-choice precedence', async ({ page }, info) => {
  await mockTodayReports(page);
  const state = await mockOnboarding(page, onboardingFixture);
  let registrations = 0;
  await page.route('**/v1/players', async route => {
    registrations++;
    const body: unknown = route.request().postDataJSON();
    if (!body || typeof body !== 'object' || !('id' in body) || typeof body.id !== 'string') throw new Error('Invalid test registration');
    state.replace({ ...state.get(), player_id: body.id });
    await route.fulfill({ json: { id: body.id, preferences: {}, access_token: 'new-player-token' } });
  });
  await page.goto('/');
  await page.getByRole('textbox', { name: 'Display name' }).fill('New player');
  await tabTo(page, page.getByRole('button', { name: 'Start learning' }));
  await page.keyboard.press('Enter');
  const choices = page.getByRole('group', { name: 'How would you describe your chess experience?' });
  await expect(choices).toBeVisible();
  await tabTo(page, page.getByRole('radio', { name: 'Not sure' }));
  await page.keyboard.press('ArrowRight');
  // Native radio groups support arrow-key selection, without custom key handlers.
  await expect(page.getByRole('radio', { name: 'Beginner' })).toBeChecked();
  await page.keyboard.press('ArrowRight');
  await expect(page.getByRole('radio', { name: 'Developing' })).toBeChecked();
  await page.keyboard.press('ArrowLeft');
  await expect(page.getByRole('radio', { name: 'Beginner' })).toBeChecked();
  await tabTo(page, page.getByRole('button', { name: 'Save level and show suggestion' }));
  await page.keyboard.press('Enter');
  await expect(page.getByRole('status').filter({ hasText: 'Suggested bot level 2.' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Your starting point' })).toBeFocused();
  await expect(page.getByRole('progressbar', { name: '0 of 5 introductory puzzles closed' })).toHaveAttribute('value', '0');
  const identity = await page.evaluate(() => localStorage.getItem('scan64_player_id'));
  await expectReflow(page);
  await page.reload();
  await expect(page.getByText('0 of 5 introductory puzzles closed')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('scan64_player_id'))).toBe(identity);
  expect(registrations).toBe(1);
  await page.getByRole('link', { name: 'Play now' }).click();
  await expect(page.getByRole('combobox', { name: 'Bot level' })).toHaveValue('2');
  await page.getByRole('combobox', { name: 'Bot level' }).selectOption('8');
  await expect(page.getByRole('combobox', { name: 'Bot level' })).toHaveValue('8');
  state.replace({ ...state.get(), suggested_bot_level: 1 });
  await page.reload();
  await expect(page.getByRole('combobox', { name: 'Bot level' })).toHaveValue('8');
  await expectReflow(page);
  await info.attach('onboarding-keyboard-and-play', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});

test('existing opt-in, keyboard skip and continue-later retain identity and multi-day progress', async ({ page }) => {
  await prepareToday(page);
  const state = await mockOnboarding(page, { ...onboardingFixture, status: 'not_offered' });
  await page.goto('/');
  await expect(page.getByRole('radio')).toHaveCount(0);
  await tabTo(page, page.getByRole('button', { name: 'Set up my starting point' }));
  await page.keyboard.press('Enter');
  await tabTo(page, page.getByRole('button', { name: 'Continue later' }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('Continue whenever you like. Your identity and progress are kept.')).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('scan64_player_id'))).toBe('player-1');
  await page.getByRole('button', { name: 'Continue introduction' }).click();
  await tabTo(page, page.getByRole('button', { name: 'Skip introduction' }));
  await page.keyboard.press('Enter');
  await expect(page.getByText('Introduction skipped. Your identity and progress are kept.')).toBeVisible();
  await page.reload();
  await expect(page.getByRole('link', { name: 'Play now' })).toBeVisible();
  state.replace({ ...state.get(), status: 'in_progress', intro_started_at: '2026-11-12T10:00:00Z', intro_started: 2, intro_closed: 2 });
  await page.reload();
  await expect(page.getByText('2 of 5 introductory puzzles closed')).toBeVisible();
  state.replace({ ...state.get(), intro_started: 4, intro_closed: 3 });
  await page.reload();
  await expect(page.getByText('3 of 5 introductory puzzles closed')).toBeVisible();
  await expectReflow(page);
  await page.route('**/v1/puzzles/active', route => route.fulfill({ json: {
    status: 'empty', run: null, message: 'No eligible daily puzzles. Play is still available.',
    daily: { utc_day: '2026-11-14', rating: 1200, rated_count: 0, provisional: true, overdue_count: 0,
      total: 0, completed: 0, remaining: 0, actual_mix: { due: 0, weak: 0, exploration: 0 }, message: '' },
    streak: { current: 0, best: 0, reset_reason: null },
  } }));
  await page.route('**/v1/puzzles/modes', route => route.fulfill({ json: { themes: [], streak: { current: 0, best: 0, reset_reason: null } } }));
  await page.getByRole('link', { name: 'Start/resume daily puzzles' }).click();
  await expect(page).toHaveURL('/puzzles');
  await expect(page.getByText('No eligible daily puzzles. Play is still available.')).toBeVisible();
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Play', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Start Game' })).toBeEnabled();
  expect(await page.evaluate(() => localStorage.getItem('scan64_player_id'))).toBe('player-1');
});
