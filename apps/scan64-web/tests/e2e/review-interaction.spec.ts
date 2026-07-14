import { test, expect } from '@playwright/test';
import { LessonSpec } from '../../src/api/types';

const mockLesson: LessonSpec = {
  schema_version: '0.1.0',
  lesson_id: 'les_1',
  source: { kind: 'pgn', fen: 'fen' },
  diagnosis: { primary: 'test', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'test', instruction: 'Find the best move' },
  interaction: { input: 'move', maximum_attempts: 1, accepted_moves: [] },
  hints: [
    { level: 1, kind: 'prompt', text: 'Hint 1' },
    { level: 2, kind: 'prompt', text: 'Hint 2', visualizations: [{ command: 'highlight', description: 'square' }] }
  ],
  explanation: { text: 'The explanation text' },
  verification: { status: 'ok', engine: 'e', engine_binary_digest: 'd', nodes: 1, multipv: 1, verified_at: 'now' },
  mastery: { skill_key: 'key', delta: 0.1 }
};

test.describe('Review Interaction Sequencing', () => {
  test('asserts no hint/arrow renders before explicit request and coach-mode-only interruption', async ({ page }) => {
    page.on('console', msg => console.log('BROWSER:', msg.text()));
    
    // Intercept API calls
    await page.route('**/v1/players', async route => {
      await route.fulfill({ json: { id: 'player-1', preferences: {}, access_token: 'test-token' } });
    });

    await page.route('**/v1/play-sessions', async route => {
      await route.fulfill({ json: { id: 'session-1', player_id: 'player-1', status: 'active' } });
    });

    await page.route('**/v1/play-sessions/*/moves', async route => {
      await route.fulfill({
        json: {
          opponent_move: 'e7e5',
          interruption_lesson: mockLesson
        }
      });
    });

    // 1. Play a game with coach mode OFF
    await page.goto('/');
    await page.getByText('Play Game').click();
    
    // Ensure coach mode is off by default
    await expect(page.getByTestId('coach-mode-toggle')).not.toBeChecked();

    await page.getByTestId('start-btn').click();
    await expect(page.getByTestId('session-info')).toBeVisible();
    
    // Trigger mock move
    await page.evaluate(async () => { await (window as any).__e2e_move(); });

    // Verify NO interruption screen
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();

    // 2. Turn coach mode ON and test interruption
    await page.goto('/');
    await page.getByText('Play Game').click();
    await page.getByTestId('coach-mode-toggle').check();
    await page.getByTestId('start-btn').click();
    await expect(page.getByTestId('session-info')).toBeVisible();

    // Trigger mock move
    await page.evaluate(async () => { await (window as any).__e2e_move(); });
    
    // Verify interruption screen appears
    await expect(page.locator('.error')).not.toBeVisible();
    await expect(page.getByTestId('critical-moment-review')).toBeVisible();

    // Verify no hint/arrow before explicit request
    await expect(page.getByTestId('hint-0')).not.toBeVisible();
    await expect(page.getByTestId('step-1-restore')).toBeVisible();
    
    // explicit request flow
    await page.getByTestId('next-step-btn').click(); // to inspect
    await page.getByTestId('next-step-btn').click(); // to request
    await page.getByTestId('request-cue-btn').click(); // to cue
    
    await expect(page.getByTestId('hint-0')).toBeVisible();
  });
});
