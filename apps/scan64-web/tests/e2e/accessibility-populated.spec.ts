import { expect, test, type Page, type TestInfo } from '@playwright/test';
import { prepareToday } from './today';

const fen = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
const long = 'LongUnbrokenPlayerAndEvidenceName'.repeat(5);
const lesson = {
  schema_version: '0.2.0', lesson_id: 'accessible-lesson', source: { kind: 'player_game', fen },
  diagnosis: { primary: 'tactics.fork', secondary: [], confidence: 1, evidence_refs: [] },
  objective: { type: 'tactic', instruction: `Find a developing move. ${long}` },
  interaction: { input: 'move', maximum_attempts: 2, accepted_moves: [{ san: 'e4', lan: 'e2e4' }] },
  hints: [{ text: `Inspect the centre. ${long}`, visualizations: [] }],
  explanation: { text: `Development helps your pieces. ${long}` },
  verification: { status: 'verified', engine: 'stockfish', engine_binary_digest: 'digest', nodes: 1, multipv: 1, verified_at: '2026-11-13T00:00:00Z' },
  mastery: { skill_key: 'tactics.fork', delta: 0.1 },
};
const evidence = { player_id: 'player-1', evidence_items: [{ evidence_id: long, kind: 'tactical-motif', position_id: long,
  claim: long, payload: {}, producer: { name: 'scan64', version: '1' } }] };
const patterns = { player_id: 'player-1', minimum_occurrences: 3, status: 'insufficient_data', recurring_diagnoses: [] };

async function capture(page: Page, info: TestInfo, state: string) {
  const widths = await page.evaluate(() => {
    const main = document.querySelector('main')!;
    return { document: [document.documentElement.scrollWidth, document.documentElement.clientWidth], main: [main.scrollWidth, main.clientWidth] };
  });
  expect(widths.document[0], `${state} document`).toBeLessThanOrEqual(widths.document[1]);
  expect(widths.main[0], `${state} main`).toBeLessThanOrEqual(widths.main[1]);
  await info.attach(`${state}-widths`, { body: JSON.stringify(widths), contentType: 'application/json' });
  await info.attach(`${state}-tree`, { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
  await info.attach(`${state}-screen`, { body: await page.screenshot({ fullPage: true }), contentType: 'image/png' });
}

for (const viewport of [{ width: 320, height: 640 }, { width: 640, height: 320 }]) {
  test.describe(`populated routes ${viewport.width}x${viewport.height}`, () => {
    test.use({ viewport, hasTouch: true, reducedMotion: 'reduce' });
    test('populated reports, library studies, lessons, live play and import reflow', async ({ page }, info) => {
      await prepareToday(page);
      await page.route('**/v1/players', async route => {
        const { id } = route.request().postDataJSON() as { id: string };
        await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
      });
      await page.route('**/v1/players/*/progress', route => route.fulfill({ json: { player_id: 'player-1', skills: [{ concept: 'tactics.fork', name: long, mastery: 0.7, uncertainty: 0.1 }] } }));
      await page.route('**/v1/players/*/evidence', route => route.fulfill({ json: evidence }));
      await page.route('**/v1/players/*/patterns', route => route.fulfill({ json: patterns }));
      await page.route('**/v1/players/*/profile', route => route.fulfill({ json: { player_id: 'player-1', rating: 1200, display_name: long } }));
      await page.route('**/v1/players/*/games*', route => route.fulfill({ json: { items: [{ id: '00000000-0000-0000-0000-000000000001', white: long, black: long, result: '1-0', date: '2026.11.13', created_at: '2026-11-13T00:00:00Z', diagnosis_count: 2 }], next_cursor: null } }));
      await page.route('**/v1/coaches/*/dashboard', route => route.fulfill({ json: { coach_id: 'player-1', students: [{ student_id: long, profile: { player_id: long, display_name: long, rating: 1200 }, evidence, patterns }] } }));
      await page.route('**/v1/learning/session?*', route => route.fulfill({ json: { session_id: 'study-1', lessons: [lesson] } }));
      await page.route('**/v1/learning/lesson-attempts', route => route.fulfill({ json: { id: 'attempt-1', success: true, grading_status: 'verified', profile_update_result: 'applied' } }));
      await page.route('**/v1/content/famous-games', route => route.fulfill({ json: [{ id: 'opera', skill_mapping: {}, payload: { title: 'Opera Game', historical_context: long, strategic_context: long, moves: ['e4'], decisions: [{ id: 'opening', ply: 0, fen, prompt: long, played_move: 'e4', accepted_moves: ['e4'], verified_alternatives: [], hints: [long], comparison: long }] } }] }));
      await page.route('**/v1/play-sessions', route => route.fulfill({ json: { id: 'accessible-play', player_id: 'player-1', opponent_config: {}, status: 'active' } }));
      await page.route('**/v1/insights/position', route => route.fulfill({ json: { step: 3, fen, visualizations: [], candidates: [{ number: 1, move: 'e2e4', san: 'e4', line: ['e2e4', 'e7e5'], reasons: [{ tier: 'principle', kind: 'centre', description: long, line: ['e2e4'], squares: ['e4'] }], fallback: false, visualizations: [] }] } }));
      await page.route('**/v1/games', route => route.fulfill({ json: { id: 'import-game', white: long, black: long, result: '*', pgn: '1. e4 *' } }));
      await page.route('**/v1/games/import-game/analysis-jobs', route => route.fulfill({ json: { id: 'analysis-1', game_id: 'import-game', status: 'completed' } }));
      await page.route('**/v1/analysis-jobs/analysis-1', route => route.fulfill({ json: { id: 'analysis-1', game_id: 'import-game', status: 'completed' } }));
      await page.route('**/v1/games/import-game/learning-opportunities?*', route => route.fulfill({ json: { session_id: 'study-1', lessons: [lesson], next_cursor: null } }));

      await page.goto('/profile');
      await expect(page.getByTestId('profile-screen')).toBeVisible();
      await capture(page, info, 'profile-populated');
      await page.goto('/coach');
      await expect(page.getByTestId('coach-dashboard')).toBeVisible();
      await capture(page, info, 'coach-populated');
      await page.goto('/games');
      await expect(page.getByRole('button', { name: /vs/ })).toBeVisible();
      await capture(page, info, 'games-populated');
      await page.goto('/training');
      await expect(page.getByTestId('lesson-instruction')).toBeVisible();
      await page.getByTestId('request-hint-button').click();
      await expect(page.getByTestId('lesson-hint')).toBeVisible();
      await capture(page, info, 'training-hint');
      await page.getByRole('textbox', { name: 'Move (UCI or SAN)' }).fill('e4');
      await page.getByRole('button', { name: 'Make move' }).click();
      await expect(page.getByTestId('lesson-explanation')).toBeVisible();
      await capture(page, info, 'training-completed');
      await page.goto('/famous');
      await page.getByRole('button', { name: 'Opera Game' }).click();
      await expect(page.getByLabel('Your move')).toBeVisible();
      await capture(page, info, 'famous-study');
      await page.goto('/explorer');
      await page.getByRole('button', { name: 'Italian Game' }).click();
      await expect(page.getByTestId('family-details')).toBeVisible();
      await capture(page, info, 'explorer-family');
      await page.goto('/play');
      await page.getByTestId('start-btn').click();
      await expect(page.getByTestId('session-info')).toContainText('Status: active');
      await page.getByRole('button', { name: 'Hint', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Preview candidate 1' })).toBeVisible();
      await capture(page, info, 'play-candidates');
      await page.goto('/import');
      await page.getByTestId('pgn-textarea').fill('[White "player-1"]\n\n1. e4 *');
      await page.getByTestId('import-btn').click();
      await expect(page.getByText(/Learning Opportunities/)).toBeVisible();
      await capture(page, info, 'import-opportunities');
    });
  });
}
