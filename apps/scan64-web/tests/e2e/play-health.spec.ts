import { expect, test, type Page } from '@playwright/test';
import { Chess } from 'chess.js';
import { movePiece } from './board';
import { prepareToday } from './today';
import type { HealthMeterRead, PositionHealthRead } from '../../src/api/types';

const GROUPS = ['king safety', 'material', 'threats', 'piece placement', 'mobility', 'pawn structure', 'passed pawns', 'space'];
const meter = (health: number, level: HealthMeterRead['level']): HealthMeterRead =>
  ({ health, level, trend: 'steady', terms: [] });

// The learner's king safety reads low once the opponent has replied 1...e5.
function healthFor(fen: string): PositionHealthRead {
  const replied = new Chess(fen).history().length === 0 && fen.includes('4p3');
  return {
    fen, learner: 'white',
    overall: { key: 'Overall', name: 'overall', learner: meter(0.6, 'fair'), opponent: meter(0.6, 'fair') },
    groups: GROUPS.map((name, index) => ({
      key: name, name,
      learner: index === 0 && replied ? meter(0.2, 'low') : meter(0.8, 'good'),
      opponent: meter(0.8, 'good'),
    })),
    outcome: null,
  };
}

type Calls = { health: string[]; counted: string[] };

async function startGame(page: Page): Promise<Calls> {
  const calls: Calls = { health: [], counted: [] };
  let playerId = 'player-1';
  let moves = 0;
  const session = () => ({
    id: 'session-1', game_id: 'game-1', player_id: playerId, player_color: 'white', opponent_config: {},
    status: 'active', coach_mode: false, assistance_modes: calls.counted.length ? ['hint'] : [],
  });
  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'token-1' } });
  });
  await page.route('**/v1/play-sessions', async route => {
    ({ player_id: playerId } = route.request().postDataJSON() as { player_id: string });
    await route.fulfill({ json: session() });
  });
  await page.route('**/v1/play-sessions/session-1', route => route.fulfill({ json: session() }));
  await page.route('**/v1/games/game-1', route => route.fulfill({
    json: { id: 'game-1', pgn: moves ? '1. e4 e5 *' : '*', white: playerId, black: 'Stockfish', result: '*' },
  }));
  await page.route('**/v1/play-sessions/session-1/moves', async route => {
    moves += 1;
    await route.fulfill({ json: { opponent_move: 'e7e5', status: 'active' } });
  });
  await page.route('**/v1/analysis/position/health', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    calls.health.push(fen);
    await route.fulfill({ json: healthFor(fen) });
  });
  await page.route('**/v1/play-sessions/session-1/health-hint', async route => {
    const { fen } = route.request().postDataJSON() as { fen: string };
    calls.counted.push(fen);
    await route.fulfill({ json: { fen, generation: 1, assistance_modes: ['hint'] } });
  });
  await page.goto('/');
  await page.getByRole('navigation', { name: 'Main navigation' }).getByRole('button', { name: 'Play', exact: true }).click();
  await page.getByTestId('start-btn').click();
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
  return calls;
}

const AFTER_E5 = 'rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2';

test.beforeEach(async ({ page }) => { await prepareToday(page); });

test('the meters show during play, update after both moves, name no move and do not count as a hint', async ({ page }) => {
  const calls = await startGame(page);
  const meters = page.getByRole('region', { name: 'Health meters' });
  await expect(page.getByTestId('play-health-setting')).toBeChecked();
  await expect(page.getByTestId('play-health-counts')).not.toBeChecked();
  await expect(meters.getByRole('img', { name: 'You king safety: good' })).toBeVisible();

  await movePiece(page, page.getByTestId('chessground-board'), 'e2', 'e4');
  await expect(meters.getByRole('img', { name: 'You king safety: low' })).toBeVisible();
  expect(calls.health).toContain(AFTER_E5);
  // No move, square or engine line appears in the meters.
  await expect(meters).not.toContainText(/\b[KQRBN]?[a-h][1-8]\b/);
  expect(calls.counted).toEqual([]);
  await expect(page.getByTestId('play-assistance-used')).toHaveCount(0);
});

test('turned off, the meters are gone and stay gone after a reload', async ({ page }) => {
  const calls = await startGame(page);
  await page.getByTestId('play-health-setting').uncheck();
  await expect(page.getByTestId('position-health')).toHaveCount(0);
  const before = calls.health.length;
  await movePiece(page, page.getByTestId('chessground-board'), 'e2', 'e4');
  await expect.poll(() => page.getByTestId('chessground-board').locator('piece').evaluateAll(pieces =>
    pieces.map(piece => (piece as HTMLElement & { cgKey?: string }).cgKey).includes('e5'))).toBe(true);
  expect(calls.health.length).toBe(before);
  await page.reload();
  await expect(page.getByTestId('session-info')).toContainText('Status: active');
  await expect(page.getByTestId('play-health-setting')).not.toBeChecked();
  await expect(page.getByTestId('position-health')).toHaveCount(0);
});

test('counted as a hint, each position on the learner\'s move is recorded once', async ({ page }) => {
  const calls = await startGame(page);
  await page.getByTestId('play-health-counts').check();
  await expect.poll(() => calls.counted).toEqual([new Chess().fen()]);
  await expect(page.getByTestId('play-assistance-used')).toContainText('hints');
  await movePiece(page, page.getByTestId('chessground-board'), 'e2', 'e4');
  await expect.poll(() => calls.counted).toEqual([new Chess().fen(), AFTER_E5]);
});
