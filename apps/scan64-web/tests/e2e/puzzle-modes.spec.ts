import { expect, test } from '@playwright/test';
import type { PuzzleRunRead } from '../../src/api/types';
import { prepareToday } from './today';

const daily = { utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
  overdue_count: 0, total: 10, completed: 0, remaining: 10,
  actual_mix: { due: 0, weak: 0, exploration: 10 }, message: 'Daily reserved, not started.' };

test('keyboard chooses modes, finishes before switching, resumes binding and exposes source mistakes at 320px', async ({ page }, testInfo) => {
  await prepareToday(page);
  await page.setViewportSize({ width: 320, height: 720 });
  let run: PuzzleRunRead | null = null;
  let streak = { current: 0, best: 0, reset_reason: null as string | null };
  const starts: unknown[] = [];
  await page.route('**/v1/puzzles/**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.endsWith('/modes')) return route.fulfill({ json: { themes: ['fork'], streak } });
    if (path.endsWith('/active')) return route.fulfill({ json: {
      status: run ? 'ready' : 'idle', run, daily, streak, message: 'Choose a practice mode, then Start.',
    } });
    if (path.endsWith('/mistakes')) return route.fulfill({ json: { items: [{
      lesson_id: 'owned-lesson', game_id: '00000000-0000-0000-0000-000000000001', ply: 7, attempts_remaining: 2, diagnosis: 'threat_processing.missed_capture',
    }], excluded: { no_review_schedule: 1, retired: 2, untrusted: 3, exhausted: 4 }, offset: 0, limit: 20, has_more: false } });
    if (path.endsWith('/runs')) {
      if (request.postData() && run?.state !== 'active') {
        const body = request.postDataJSON(); starts.push(body);
        run = { run_id: `run-${starts.length}`, puzzle_id: `puzzle-${starts.length}`,
          fen: 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1', setup: 'e2e4',
          played_moves: [], cursor: 0, revision: 0, wrong_attempts: 0, maximum_wrong_attempts: 3,
          state: 'active', hint_step: 0, hints: [], selection_reason: body.mode,
          widening: '200', rated: false, mode: body.mode, theme: body.theme ?? null, streak, daily };
      }
      return route.fulfill({ json: { status: run ? 'ready' : 'exhausted', run, daily, streak, message: run ? 'Run resumed.' : 'Daily complete.' } });
    }
    if (!run) throw new Error('No active mode fixture');
    if (path.endsWith('/abandon')) run = { ...run, state: 'abandoned', revision: run.revision + 1 };
    else if (path.endsWith('/moves')) {
      expect(request.postDataJSON().move).toBe('e7e5');
      streak = { current: 1, best: 1, reset_reason: null };
      run = { ...run, state: 'solved', cursor: 1, revision: 1, played_moves: ['e7e5'], streak,
        fen: 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2' };
    }
    return route.fulfill({ json: run });
  });
  await page.goto('/puzzles');
  const themeRadio = page.getByRole('radio', { name: 'Theme', exact: true });
  await expect(themeRadio).toBeEnabled();
  await themeRadio.focus(); await page.keyboard.press('Space');
  await page.keyboard.press('Tab');
  await expect(page.getByRole('combobox', { name: 'Theme', exact: true })).toBeFocused();
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Abandon puzzle', exact: true })).toBeEnabled();
  expect(starts).toEqual([{ mode: 'theme', theme: 'fork' }]);
  await expect(page.getByRole('radio', { name: 'Solve streak', exact: true })).toBeDisabled();
  await page.reload();
  await expect(themeRadio).toBeChecked();
  await expect(page.getByText('Finish or abandon the active puzzle before switching modes.')).toBeVisible();
  await page.getByRole('button', { name: 'Abandon puzzle', exact: true }).focus(); await page.keyboard.press('Enter');
  const streakRadio = page.getByRole('radio', { name: 'Solve streak', exact: true });
  await streakRadio.focus(); await page.keyboard.press('Space');
  await page.keyboard.press('Tab'); await page.keyboard.press('Enter');
  const move = page.getByRole('textbox', { name: 'Move (UCI or SAN)' });
  await move.focus(); await page.keyboard.type('e5'); await page.keyboard.press('Enter');
  await expect(page.getByText('Current: 1 · Best: 1')).toBeVisible();
  const mistakesRadio = page.getByRole('radio', { name: 'Your mistakes', exact: true });
  await mistakesRadio.focus(); await page.keyboard.press('Space');
  await expect(page.getByRole('link', { name: /ply 7/ })).toHaveAttribute('href', /analysis\?ply=7$/);
  await expect(page.getByRole('button', { name: 'Start mistake at ply 7' })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await testInfo.attach('mode-keyboard-tree', { body: await page.locator('main').ariaSnapshot(), contentType: 'text/plain' });
});
