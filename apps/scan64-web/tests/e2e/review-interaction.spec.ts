import { expect, test } from '@playwright/test';
import type { Locator, Page } from '@playwright/test';

import { attemptMovePiece, movePiece } from './board';

const serverLesson = {
  schema_version: '0.1.0',
  lesson_id: 'lesson-123',
  source: {
    kind: 'pgn',
    fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
  },
  diagnosis: {
    primary: 'board_awareness.hanging_piece',
    secondary: [],
    confidence: 1,
    evidence_refs: [],
  },
  objective: { type: 'tactic', instruction: 'Find the best move.' },
  interaction: { input: 'move', maximum_attempts: 1, accepted_moves: ['e2e4'] },
  hints: [],
  explanation: { text: 'The committed server lesson.' },
  verification: {
    status: 'verified',
    engine: 'stockfish',
    engine_binary_digest: 'digest',
    nodes: 1,
    multipv: 1,
    verified_at: '2026-07-31T00:00:00Z',
  },
  mastery: { skill_key: 'board_awareness.hanging_piece', delta: 0.1 },
};

async function routePlayerAndTraining(page: Page): Promise<void> {
  await page.route('**/v1/players', async route => {
    const { id } = route.request().postDataJSON() as { id: string };
    await route.fulfill({ json: { id, preferences: {}, access_token: 'test-token' } });
  });
  await page.route('**/v1/learning/session?*', async route => {
    await route.fulfill({ json: { session_id: 'study-1', lessons: [] } });
  });
}

async function expectBoardPosition(board: Locator, expectedKeys: string[]): Promise<void> {
  await expect.poll(async () =>
    board.locator('piece').evaluateAll(
      (pieces, expected) => {
        const keys = pieces.map((piece) => (piece as HTMLElement & { cgKey?: string }).cgKey);
        return expected.every(key => keys.includes(key));
      },
      expectedKeys,
    ),
  ).toBe(true);
}

test.describe('Coach interruption interaction', () => {
  test('answers a committed coach interruption with real pointer input', async ({ page }) => {
    let createPayload: { coach_mode?: boolean } | null = null;
    let moveRequests = 0;
    let recordedAttempt: Record<string, unknown> | null = null;
    await routePlayerAndTraining(page);
    await page.route('**/v1/play-sessions', async route => {
      createPayload = route.request().postDataJSON() as { coach_mode?: boolean };
      await route.fulfill({
        json: {
          id: 'session-1',
          player_id: 'player-1',
          status: 'active',
          opponent_config: {},
          coach_mode: true,
        },
      });
    });
    await page.route('**/v1/play-sessions/*/moves', async route => {
      moveRequests += 1;
      if (moveRequests === 1) {
        await route.fulfill({
          json: {
            opponent_move: 'e7e5',
            status: 'active',
            critical_interruption: {
              lesson: serverLesson,
              opportunity_id: 'opportunity-123',
              study_session_id: 'study-123',
            },
          },
        });
        return;
      }
      await route.fulfill({
        json: {
          opponent_move: 'b8c6',
          status: 'active',
          critical_interruption: null,
        },
      });
    });
    await page.route('**/v1/learning/lesson-attempts', async route => {
      recordedAttempt = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({
        json: {
          id: 'attempt-123',
          success: true,
          grading_status: 'verified',
          profile_update_result: 'applied',
        },
      });
    });

    await page.goto('/');
    await page.getByText('Play Game').click();
    await page.getByTestId('coach-mode-toggle').check();
    await page.getByTestId('start-btn').click();
    const gameBoard = page.getByTestId('chessground-board');
    await expect(gameBoard).toBeVisible();

    await movePiece(page, gameBoard, 'e2', 'e4');
    await expectBoardPosition(gameBoard, ['e4', 'e5']);
    const lessonBoard = page.getByTestId('lesson-board');
    await expect(lessonBoard).toHaveAttribute('aria-busy', 'false');
    await movePiece(page, lessonBoard, 'e2', 'e4');

    await expect(page.getByRole('alert')).toHaveText('Correct. Attempt recorded.');
    await attemptMovePiece(page, gameBoard, 'g1', 'f3');
    await expect(gameBoard.locator('square.selected')).toHaveCount(0);
    await expectBoardPosition(gameBoard, ['e4', 'e5']);
    await expect.poll(async () =>
      gameBoard.locator('piece').evaluateAll(
        pieces => pieces.some(piece => (piece as HTMLElement & { cgKey?: string }).cgKey === 'f3'),
      ),
    ).toBe(false);
    expect(createPayload).toEqual(expect.objectContaining({ coach_mode: true }));
    expect(moveRequests).toBe(1);
    expect(recordedAttempt).toEqual(
      expect.objectContaining({
        session_id: 'study-123',
        lesson_id: 'opportunity-123',
        source_kind: 'persisted_opportunity',
        submitted_move: 'e2e4',
      }),
    );

    await page.getByTestId('next-step-btn').click();
    await page.getByTestId('next-step-btn').click();
    await page.getByTestId('request-cue-btn').click();
    await page.getByTestId('replay-btn').click();
    await page.getByTestId('complete-btn').click();
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();
    await movePiece(page, gameBoard, 'g1', 'f3');
    await expectBoardPosition(gameBoard, ['f3', 'c6']);
    expect(moveRequests).toBe(2);
  });

  test('does not hand off interruption context for ordinary non-coach play', async ({ page }) => {
    let createPayload: { coach_mode?: boolean } | null = null;
    await routePlayerAndTraining(page);
    let moveRequests = 0;
    await page.route('**/v1/play-sessions', async route => {
      createPayload = route.request().postDataJSON() as { coach_mode?: boolean };
      await route.fulfill({
        json: {
          id: 'session-1',
          player_id: 'player-1',
          status: 'active',
          opponent_config: {},
          coach_mode: false,
        },
      });
    });
    await page.route('**/v1/play-sessions/*/moves', async route => {
      moveRequests += 1;
      await route.fulfill({
        json: {
          opponent_move: 'e7e5',
          status: 'active',
          critical_interruption: {
            lesson: serverLesson,
            opportunity_id: 'unexpected-opportunity',
            study_session_id: 'unexpected-study-session',
          },
        },
      });
    });

    await page.goto('/');
    await page.getByText('Play Game').click();
    await page.getByTestId('start-btn').click();
    const gameBoard = page.getByTestId('chessground-board');
    await expect(gameBoard).toBeVisible();
    await movePiece(page, gameBoard, 'e2', 'e4');
    await expectBoardPosition(gameBoard, ['e4', 'e5']);

    expect(createPayload).toEqual(expect.objectContaining({ coach_mode: false }));
    expect(moveRequests).toBe(1);
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();
    await expect(page.locator('.error')).not.toBeVisible();
  });

  test('continues coach play when the server returns no interruption', async ({ page }) => {
    let moveRequests = 0;
    await routePlayerAndTraining(page);
    await page.route('**/v1/play-sessions', async route => {
      await route.fulfill({
        json: {
          id: 'session-1',
          player_id: 'player-1',
          status: 'active',
          opponent_config: {},
          coach_mode: true,
        },
      });
    });
    await page.route('**/v1/play-sessions/*/moves', async route => {
      moveRequests += 1;
      await route.fulfill({
        json: {
          opponent_move: 'e7e5',
          status: 'active',
          critical_interruption: null,
        },
      });
    });

    await page.goto('/');
    await page.getByText('Play Game').click();
    await page.getByTestId('coach-mode-toggle').check();
    await page.getByTestId('start-btn').click();
    const gameBoard = page.getByTestId('chessground-board');
    await expect(gameBoard).toBeVisible();
    await movePiece(page, gameBoard, 'e2', 'e4');

    await expectBoardPosition(gameBoard, ['e4', 'e5']);
    expect(moveRequests).toBe(1);
    await expect(page.getByTestId('critical-moment-review')).not.toBeVisible();
    await expect(page.locator('.error')).not.toBeVisible();
  });
});
