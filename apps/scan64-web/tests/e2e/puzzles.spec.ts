import { expect, test } from '@playwright/test';
import { movePiece } from './board';
import { prepareToday } from './today';
import type { PuzzleRunRead } from '../../src/api/types';

const SETUP = 'rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1';
const REPLY = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R b KQkq - 1 2';
const FINISH = 'r1bqkbnr/pppp1ppp/2n5/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 2 3';

// Only already-played moves cross the boundary. The test backend keeps its source line private.
test('navigates to puzzles, rejects a wrong move, resumes hints on reload, plays automatic replies and solves', async ({ page }) => {
  await prepareToday(page);
  let run: PuzzleRunRead = { run_id: 'run-1', puzzle_id: 'puzzle-1', fen: SETUP, setup: 'e2e4',
    played_moves: [], cursor: 0, revision: 0, wrong_attempts: 0, maximum_wrong_attempts: 3,
    state: 'active', hint_step: 0, hints: [],
    mode: 'daily', theme: null, streak: { current: 0, best: 0, reset_reason: null },
    selection_reason: 'exploration', widening: '200', rated: true,
    daily: { utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
      overdue_count: 0, total: 1, completed: 0, remaining: 1,
      actual_mix: { due: 0, weak: 0, exploration: 1 }, message: 'Daily set reserved.' } };
  await page.route('**/v1/puzzles/modes', route => route.fulfill({
    json: { themes: ['fork'], streak: run.streak },
  }));
  const mutations: { path: string; body: Record<string, unknown> }[] = [];
  let starts = 0;
  await page.route('**/v1/puzzles/{runs,active}**', async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === '/v1/puzzles/active' && request.method() === 'GET') {
      starts++;
      expect(request.postData()).toBeNull();
      await route.fulfill({ json: { status: 'ready', run, message: '' } });
      return;
    }
    if (request.method() === 'GET') {
      await route.fulfill({ json: run });
      return;
    }
    const body = request.postDataJSON() as Record<string, unknown>;
    mutations.push({ path, body });
    expect(body.expected_cursor).toBe(run.cursor);
    expect(body.expected_revision).toBe(run.revision);
    expect(body).not.toHaveProperty('player_id');
    if (path.endsWith('/hints')) {
      expect(body.step).toBe(1);
      run = { ...run, revision: run.revision + 1, hint_step: 1,
        hints: [{ step: 1, text: 'Inspect the central pawns.', visualizations: [] }] };
    } else if (path.endsWith('/moves') && body.move === 'a7a6') {
      run = { ...run, revision: run.revision + 1, wrong_attempts: run.wrong_attempts + 1 };
    } else if (path.endsWith('/moves') && body.move === 'e7e5' && run.cursor === 0) {
      run = { ...run, fen: REPLY, cursor: 2, revision: run.revision + 1,
        played_moves: ['e7e5', 'g1f3'], hints: [], hint_step: 0 };
    } else if (path.endsWith('/moves') && body.move === 'b8c6' && run.cursor === 2) {
      run = { ...run, fen: FINISH, cursor: 3, revision: run.revision + 1,
        played_moves: ['e7e5', 'g1f3', 'b8c6'], state: 'solved' };
    } else {
      throw new Error(`Unexpected puzzle mutation ${path}: ${JSON.stringify(body)}`);
    }
    await route.fulfill({ json: run });
  });

  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Puzzles', exact: true }).click();
  await expect(page).toHaveURL(/\/puzzles$/);
  await expect(page.getByRole('heading', { name: 'Puzzles', exact: true })).toBeVisible();
  const board = page.getByTestId('puzzle-board');
  await expect(board).toHaveClass(/orientation-black/);
  await expect(page.getByText('3 wrong attempts remaining', { exact: true })).toBeVisible();
  // The development entrypoint double-mounts effects under React StrictMode.
  await expect.poll(() => starts).toBe(2);

  await movePiece(page, board, 'a7', 'a6');
  await expect(page.getByText('2 wrong attempts remaining', { exact: true })).toBeVisible();
  expect(run.cursor).toBe(0);
  expect(run.fen).toBe(SETUP);
  await expect.poll(() => board.locator('piece.black.pawn').evaluateAll(pieces =>
    pieces.some(piece => (piece as HTMLElement & { cgKey?: string }).cgKey === 'a7'),
  )).toBe(true);
  await page.getByRole('button', { name: 'Show hint', exact: true }).click();
  await expect(page.getByText('Inspect the central pawns.', { exact: true })).toBeVisible();

  await page.reload();
  await expect(page.getByText('Inspect the central pawns.', { exact: true })).toBeVisible();
  await expect(page.getByText('2 wrong attempts remaining', { exact: true })).toBeVisible();
  await expect(board).toHaveClass(/orientation-black/);
  await expect.poll(() => starts).toBe(4);
  expect(mutations).toHaveLength(2);

  // e7 is still occupied after the rejected move; after acceptance the board must
  // include White's automatic Nf3 reply before Black can play the next source move.
  await movePiece(page, board, 'e7', 'e5');
  await expect.poll(() => run.cursor).toBe(2);
  await expect(page.getByText('Inspect the central pawns.', { exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Show hint', exact: true })).toBeEnabled();
  await expect.poll(() => board.locator('piece.white.knight').evaluateAll(pieces =>
    pieces.some(piece => (piece as HTMLElement & { cgKey?: string }).cgKey === 'f3'),
  )).toBe(true);
  await expect(board).toHaveClass(/orientation-black/);
  await page.reload();
  await expect(page.getByText('2 wrong attempts remaining', { exact: true })).toBeVisible();
  await expect.poll(() => board.locator('piece.white.knight').evaluateAll(pieces =>
    pieces.some(piece => (piece as HTMLElement & { cgKey?: string }).cgKey === 'f3'),
  )).toBe(true);
  await expect.poll(() => starts).toBe(6);
  expect(mutations).toHaveLength(3);
  await movePiece(page, board, 'b8', 'c6');
  await expect(page.getByText(/solved/i)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Next puzzle', exact: true })).toBeEnabled();
  expect(mutations.filter(item => item.path.endsWith('/moves')).map(item => item.body)).toEqual([
    { expected_cursor: 0, expected_revision: 0, move: 'a7a6' },
    { expected_cursor: 0, expected_revision: 2, move: 'e7e5' },
    { expected_cursor: 2, expected_revision: 3, move: 'b8c6' },
  ]);
});
