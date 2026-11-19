import { expect, test } from '@playwright/test';
import { Chess } from 'chess.js';
import { makeGameReview } from '../../src/components/review.test-fixtures';
import { checkUserDrawing, movePiece } from './board';
import { prepareToday } from './today';

for (const outcome of ['legal', 'illegal', 'cancelled'] as const) {
  test(`Play ${outcome} premove waits for the server reply, with drawing and flip`, async ({ page }) => {
    await prepareToday(page);
    await page.addInitScript(() => localStorage.setItem('scan64_active_play_session_id', 'controls-play'));
    const session = { id: 'controls-play', player_id: 'player-1', game_id: 'controls-game',
      player_color: 'white', coach_mode: false, assistance_modes: [], opponent_config: {}, status: 'active' };
    await page.route('**/v1/play-sessions/controls-play', route => route.fulfill({ json: session }));
    await page.route('**/v1/games/controls-game', route => route.fulfill({ json: { id: 'controls-game', pgn: '*', result: '*' } }));
    const submitted: string[] = [];
    const reply = Promise.withResolvers<void>();
    await page.route('**/v1/play-sessions/controls-play/moves', async route => {
      submitted.push(route.request().postDataJSON().move);
      if (submitted.length === 1) {
        await reply.promise;
        await route.fulfill({ json: { opponent_move: 'e7e5', status: 'active' } });
      } else await route.fulfill({ json: { opponent_move: 'b8c6', status: 'active' } });
    });
    await page.goto('/play');
    const board = page.getByTestId('chessground-board');
    await checkUserDrawing(page, board);
    await page.getByRole('button', { name: 'Flip board' }).click();
    await expect(board).toHaveClass(/orientation-black/);
    await page.getByText('Read board position', { exact: true }).click();
    await expect(page.getByText(/Board viewed from Black/)).toBeVisible();
    await movePiece(page, board, 'e2', 'e4');
    await expect.poll(() => submitted).toEqual(['e2e4']);
    await movePiece(page, board, outcome === 'illegal' ? 'e4' : 'g1', outcome === 'illegal' ? 'd5' : 'f3');
    await expect(page.getByRole('button', { name: 'Cancel premove' })).toBeVisible();
    expect(submitted).toEqual(['e2e4']);
    if (outcome === 'cancelled') await page.getByRole('button', { name: 'Cancel premove' }).click();
    reply.resolve();
    await expect(board).toHaveAttribute('aria-busy', 'false');
    await expect(page.getByRole('button', { name: 'Cancel premove' })).toHaveCount(0);
    if (outcome === 'legal') {
      await expect.poll(() => submitted).toEqual(['e2e4', 'g1f3']);
      await expect(page.getByTestId('chessground-board-san-move-list')).toContainText('Nf3');
    } else expect(submitted).toEqual(['e2e4']);
  });
}

test('Analysis and Review drawing and flip preserve the readable selected position', async ({ page }) => {
  await prepareToday(page);
  await page.goto('/analysis');
  await checkUserDrawing(page, page.getByTestId('free-analysis-board'));
  await page.getByRole('button', { name: 'Flip board' }).click();
  await expect(page.getByTestId('free-analysis-board')).toHaveClass(/orientation-black/);
  await page.getByText('Read board position', { exact: true }).click();
  await expect(page.getByRole('list', { name: 'Pieces by square' })).toContainText('e2: White pawn');
  await expect(page.getByText(/Board viewed from Black/)).toBeVisible();
  const gameId = '00000000-0000-0000-0000-000000000001';
  await page.route(`**/v1/games/${gameId}/review`, route => route.fulfill({ json: makeGameReview({ game_id: gameId }) }));
  await page.route(`**/v1/games/${gameId}/analysis-status`, route => route.fulfill({ json: { status: 'completed' } }));
  await page.goto(`/games/${gameId}/analysis`);
  const board = page.getByTestId('review-board');
  await checkUserDrawing(page, board);
  await page.getByRole('button', { name: 'Initial position', exact: true }).first().click();
  const orientation = await board.getAttribute('class');
  await page.getByRole('button', { name: 'Flip board' }).click();
  await expect(board).not.toHaveAttribute('class', orientation!);
  await expect(page.getByTestId('review-selected-position')).toContainText('Initial position');
  await page.getByText('Read board position', { exact: true }).click();
  await expect(page.getByRole('list', { name: 'Pieces by square' })).toContainText('e2: White pawn');
});

test('Puzzle drawings and flip do not submit attempts or enable premoves', async ({ page }) => {
  await prepareToday(page);
  const chess = new Chess(); chess.move('e4');
  const daily = { utc_day: '2026-11-17', rating: 1200, rated_count: 0, provisional: true,
    overdue_count: 0, total: 1, completed: 0, remaining: 1, actual_mix: { due: 0, weak: 0, exploration: 1 }, message: 'Ready.' };
  const streak = { current: 0, best: 0, reset_reason: null };
  const run = { run_id: 'controls-run', puzzle_id: 'controls-puzzle', fen: chess.fen(), setup: 'e2e4',
    played_moves: [], cursor: 0, revision: 0, wrong_attempts: 0, maximum_wrong_attempts: 3,
    state: 'active', hint_step: 1, hints: [{ step: 1, text: 'Develop.', visualizations: [
      { command: 'draw_arrow', origin: 'e7', target: 'e5', description: 'Coach annotation' },
    ] }], mode: 'daily', theme: null, streak, daily, selection_reason: 'exploration', widening: '200', rated: true };
  let attempts = 0;
  await page.route('**/v1/puzzles/modes', route => route.fulfill({ json: { themes: [], streak } }));
  await page.route('**/v1/puzzles/active*', route => route.fulfill({ json: { status: 'ready', run, daily, streak, message: 'Ready.' } }));
  await page.route('**/v1/puzzles/runs/*/moves', route => { attempts++; return route.fulfill({ json: run }); });
  await page.goto('/puzzles');
  const board = page.getByTestId('puzzle-board');
  await checkUserDrawing(page, board);
  await page.getByRole('button', { name: 'Flip board' }).click();
  await expect(board).toHaveClass(/orientation-white/);
  await page.getByText('Read board position', { exact: true }).click();
  await expect(page.getByText(/Board viewed from White/)).toBeVisible();
  await expect(page.getByRole('list', { name: 'Pieces by square' })).toContainText('e4: White pawn');
  expect(attempts).toBe(0);
  await expect(page.getByRole('button', { name: 'Cancel premove' })).toHaveCount(0);
});

test('Move sounds default on and a muted preference survives a page restart', async ({ page }) => {
  await prepareToday(page);
  await page.goto('/settings');
  const sounds = page.getByRole('checkbox', { name: 'Move sounds' });
  await expect(sounds).toBeChecked();
  await sounds.uncheck();
  await page.reload();
  await expect(sounds).not.toBeChecked();
  expect(await page.evaluate(() => localStorage.getItem('scan64-board-sounds'))).toBe('false');
});
