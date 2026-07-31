import { expect, test } from '@playwright/test';
import { movePiece } from './board';

const lesson = {
  schema_version: '1.0',
  lesson_id: '00000000-0000-0000-0000-000000000001',
  source: { kind: 'position', fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1' },
  diagnosis: { primary: 'tactics.fork', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'find_best_move', instruction: 'Play e4.' },
  interaction: { input: 'click', maximum_attempts: 3, accepted_moves: [{ san: 'e4' }] },
  hints: [{ level: 1, kind: 'prompt', text: 'Control the centre.' }],
  explanation: { text: 'e4 controls the centre.' },
  verification: { status: 'verified', engine: 'stockfish' },
};

test('records an accepted daily-training move from real board pointer input', async ({ page }) => {
  let attempt: Record<string, unknown> | undefined;
  await page.addInitScript(() => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'test-token');
  });
  await page.route('**/v1/players', async route => {
    await route.fulfill({ json: { id: 'player-1', preferences: {}, access_token: 'test-token' } });
  });
  await page.route('**/v1/learning/session?*', async route => {
    await route.fulfill({ json: { session_id: 'study-1', lessons: [lesson] } });
  });
  await page.route('**/v1/learning/lesson-attempts', async route => {
    attempt = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { id: 'attempt-1', success: true, grading_status: 'verified', profile_update_result: 'applied' } });
  });

  await page.goto('/');
  await page.getByText('Daily Training').click();
  const board = page.getByTestId('lesson-board');
  await movePiece(page, board, 'e2', 'e4');

  await expect(page.getByTestId('lesson-feedback')).toHaveText('Correct. Attempt recorded.');
  expect(attempt).toMatchObject({
    session_id: 'study-1',
    lesson_id: lesson.lesson_id,
    source_kind: 'persisted_opportunity',
    submitted_move: 'e2e4',
    hints_used: 0,
  });
});
