import { expect, test } from '@playwright/test';
import { movePiece } from './board';
import { prepareToday } from './today';

test('under-promotes on the shared board, then resigns and opens game review', async ({ page }) => {
  await prepareToday(page);
  await page.addInitScript(() => localStorage.setItem('scan64_active_play_session_id', 'promotion-session'));
  const gameId = '00000000-0000-4000-8000-000000000052';
  const session = { id: 'promotion-session', player_id: 'player-1', game_id: gameId,
    opponent_config: { strength: '0' }, coach_mode: false, status: 'active' };
  let submittedMove: string | null = null;
  let resigned = false;
  await page.route('**/v1/play-sessions/promotion-session', route => route.fulfill({ json: session }));
  await page.route(`**/v1/games/${gameId}`, route => route.fulfill({ json: {
    id: gameId, white: 'player-1', black: 'Stockfish', result: resigned ? '0-1' : '*',
    pgn: '[SetUp "1"]\n[FEN "8/P7/8/8/8/8/8/k6K w - - 0 1"]\n\n*',
  } }));
  await page.route('**/v1/play-sessions/promotion-session/moves', async route => {
    const payload: unknown = route.request().postDataJSON();
    if (!payload || typeof payload !== 'object' || !('move' in payload) || typeof payload.move !== 'string') {
      throw new Error('Expected a UCI move request');
    }
    submittedMove = payload.move;
    await route.fulfill({ json: { opponent_move: 'a1b1', status: 'active' } });
  });
  await page.route('**/v1/play-sessions/promotion-session/resign', async route => {
    expect(route.request().headers().authorization).toBe('Bearer test-token');
    resigned = true;
    await route.fulfill({ json: { ...session, status: 'completed' } });
  });
  await page.route(`**/v1/games/${gameId}/analysis-status`, route => route.fulfill({ json: { status: 'not_analysed' } }));
  await page.route(`**/v1/games/${gameId}/positions`, route => route.fulfill({ json: [] }));

  await page.goto('/play');
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
  await movePiece(page, page.getByTestId('chessground-board'), 'a7', 'a8');
  for (const piece of ['queen', 'rook', 'bishop', 'knight']) {
    await expect(page.getByRole('button', { name: `Promote to ${piece}` })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Promote to rook' }).click();
  await expect.poll(() => submittedMove).toBe('a7a8r');
  await expect(page.getByTestId('chessground-board-san-move-list')).toContainText('a8=R+');
  await expect(page.getByTestId('chessground-board-san-move-list')).toContainText('Kb1');
  await page.getByRole('button', { name: 'Resign', exact: true }).click();
  await expect(page.getByTestId('game-result')).toContainText('You resigned. Opponent wins.');
  await expect(page.getByRole('link', { name: 'Review game' })).toHaveAttribute('href', `/games/${gameId}/analysis`);
  await page.getByRole('link', { name: 'Review game' }).click();
  await expect(page).toHaveURL(new RegExp(`/games/${gameId}/analysis$`));
  await expect(page.getByTestId('analysis-not-analysed')).toBeVisible();
});
