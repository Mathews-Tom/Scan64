import type { Page } from '@playwright/test';
import { onboardingFixture, strengthFixture } from '../../src/components/strength.test-fixtures';

export async function mockTodayReports(page: Page): Promise<void> {
  await page.route('**/v1/players/*/onboarding', route => route.fulfill({ json: { ...onboardingFixture, status: 'not_offered' } }));
  await page.route('**/v1/players/*/strength', route => route.fulfill({ json: strengthFixture }));
  await page.route('**/v1/players/*/bot-choice', route => {
    const body: unknown = route.request().postDataJSON();
    if (!body || typeof body !== 'object' || !('bot_level' in body) || typeof body.bot_level !== 'number') {
      return route.fulfill({ status: 422, json: { detail: 'Invalid bot level' } });
    }
    return route.fulfill({ json: { ...onboardingFixture, manual_bot_level: body.bot_level, preferred_bot_level: body.bot_level } });
  });
  await page.route('**/v1/puzzles/daily', route => route.fulfill({ json: {
    utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
    overdue_count: 2, total: 10, completed: 3, remaining: 7,
    actual_mix: { due: 4, weak: 5, exploration: 1 },
    message: "Today's stable set resets at 00:00 UTC.",
  } }));
  await page.route('**/v1/bots', route => route.fulfill({ json: [
    { level: 1, name: 'Beginner fixture', description: 'A friendly opponent' },
  ] }));
  await page.route('**/v1/players/*/games*', route => route.fulfill({ json: { items: [], next_cursor: null } }));
  await page.route('**/v1/reports/weekly?*', route => route.fulfill({ json: {
    player_id: 'player-1', games_played: 0, active_concepts_observed: 0,
    active_mastery: [], top_recurring_diagnosis: null,
  } }));
  await page.route('**/v1/players/*/patterns', route => route.fulfill({ json: {
    player_id: 'player-1', minimum_occurrences: 3, status: 'insufficient_data', recurring_diagnoses: [],
  } }));
}

export async function prepareToday(page: Page): Promise<void> {
  await page.addInitScript(() => {
    if (!localStorage.getItem('scan64_player_id')) {
      localStorage.setItem('scan64_player_id', 'player-1');
      localStorage.setItem('scan64_player_token:player-1', 'test-token');
    }
  });
  await mockTodayReports(page);
}
