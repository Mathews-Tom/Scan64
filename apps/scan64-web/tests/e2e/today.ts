import type { Page } from '@playwright/test';

export async function mockTodayReports(page: Page): Promise<void> {
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
