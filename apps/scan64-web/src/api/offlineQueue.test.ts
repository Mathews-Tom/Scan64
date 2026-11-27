import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlayMoveResponse } from './types';
import { Chess } from 'chess.js';
import { ApiClient, BotPlayUnavailableError } from './client';
import { getQueuedMoves, QUEUED_MOVE_SYNC_FAILED, QUEUED_MOVE_SYNC_SUCCEEDED, syncQueuedMoves } from './offlineQueue';
import type { QueuedMove } from './offlineQueue';

const storage = vi.hoisted(() => ({ queue: [] as QueuedMove[] }));

vi.mock('./client', () => ({
  BotPlayUnavailableError: class BotPlayUnavailableError extends Error {
    readonly status = 503;
    readonly reason: string | null;
    constructor(reason: string | null) {
      super('Bot play is unavailable under the active engine profile.');
      this.reason = reason;
    }
  },
  ApiClient: {
    makePlaySessionMove: vi.fn(),
    getPlaySession: vi.fn(),
    getGame: vi.fn(),
  },
}));

vi.mock('idb-keyval', () => ({
  get: vi.fn(async () => structuredClone(storage.queue)),
  set: vi.fn(async (_key: IDBValidKey, value: QueuedMove[]) => {
    storage.queue = structuredClone(value);
  }),
  update: vi.fn(async (
    _key: IDBValidKey,
    updater: (queue: QueuedMove[] | undefined) => QueuedMove[],
  ) => {
    storage.queue = updater(structuredClone(storage.queue));
  }),
}));

describe('syncQueuedMoves', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    storage.queue = [
      { sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn: '' },
    ];
    vi.mocked(ApiClient.getPlaySession).mockResolvedValue({
      id: 'sess-123', player_id: 'player', game_id: 'game-123', status: 'active',
      opponent_config: { assistance_generation: '7' }, coach_mode: false, assistance_modes: [],
    });
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: '', white: 'player', black: 'Stockfish', result: '*',
    });
  });

  it('shares one drain while a queued move is in flight', async () => {
    const moveRequest = Promise.withResolvers<PlayMoveResponse>();
    vi.mocked(ApiClient.makePlaySessionMove).mockReturnValueOnce(moveRequest.promise);

    const firstDrain = syncQueuedMoves();
    const secondDrain = syncQueuedMoves();

    expect(secondDrain).toBe(firstDrain);
    await vi.waitFor(() => {
      expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    });

    moveRequest.resolve({ opponent_move: 'e7e5', status: 'active' });
    await firstDrain;

    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledWith('sess-123', {
      move: 'e2e4',
      expected_generation: 7,
    });
  });

  it.each(['1. e4 *', '1. e4 e5 *', '1. e4 e5 2. Nf3 *'])(
    'acknowledges an already saved human move without replaying it (%s)', async pgn => {
      vi.mocked(ApiClient.getGame).mockResolvedValue({
        id: 'game-123', pgn, white: 'player', black: 'Stockfish', result: '*',
      });
      const acknowledged = vi.fn();
      window.addEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, acknowledged);
      try {
        await syncQueuedMoves();
        expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
        expect(await getQueuedMoves()).toEqual([]);
        expect(acknowledged.mock.calls[0][0].detail.response).toBeNull();
      } finally {
        window.removeEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, acknowledged);
      }
    },
  );

  it('reconciles a human commit after bot failure without a second submission', async () => {
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce({
      id: 'game-123', pgn: '', white: 'player', black: 'Stockfish', result: '*',
    }).mockResolvedValueOnce({
      id: 'game-123', pgn: '1. e4 *', white: 'player', black: 'Stockfish', result: '*',
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new Error('bot unavailable'));
    await syncQueuedMoves();
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('sess-123', {
      move: 'e2e4', expected_generation: 7,
    });
    expect(await getQueuedMoves()).toEqual([]);
  });

  it('reports bot play unavailable after the human move commits', async () => {
    vi.mocked(ApiClient.getGame).mockResolvedValueOnce({
      id: 'game-123', pgn: '', white: 'player', black: 'Stockfish', result: '*',
    }).mockResolvedValueOnce({
      id: 'game-123', pgn: '1. e4 *', white: 'player', black: 'Stockfish', result: '*',
    });
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new BotPlayUnavailableError('no Skill Level'));
    const acknowledged = vi.fn();
    window.addEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, acknowledged);
    try {
      await syncQueuedMoves();
      expect(await getQueuedMoves()).toEqual([]);
      expect(acknowledged.mock.calls[0][0].detail).toMatchObject({
        response: null, botPlayUnavailable: 'Bot play is unavailable under the active engine profile.',
      });
    } finally {
      window.removeEventListener(QUEUED_MOVE_SYNC_SUCCEEDED, acknowledged);
    }
  });

  it('retains a rejected submission when the human move was not saved', async () => {
    const queuedBefore = await getQueuedMoves();
    vi.mocked(ApiClient.makePlaySessionMove).mockRejectedValue(new Error('position changed'));
    await syncQueuedMoves();
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledTimes(1);
    expect(await getQueuedMoves()).toEqual(queuedBefore);
  });

  it.each([
    { sourcePgn: '', canonical: '1. d4 d5 *' },
    { sourcePgn: '1. e4 e5 *', canonical: '' },
    { sourcePgn: undefined, canonical: '1. e4 *' },
  ])('retains unmatched or unknown queued intent ($sourcePgn)', async ({ sourcePgn, canonical }) => {
    const queuedMoves = [
      { sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn },
    ];
    storage.queue = queuedMoves;
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: canonical, white: 'player', black: 'Stockfish', result: '*',
    });
    const failed = vi.fn();
    window.addEventListener(QUEUED_MOVE_SYNC_FAILED, failed);
    try {
      await syncQueuedMoves();
      expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
      expect(await getQueuedMoves()).toEqual(queuedMoves);
      expect(failed.mock.calls[0][0].detail.queuedMove.move).toBe('e2e4');
    } finally {
      window.removeEventListener(QUEUED_MOVE_SYNC_FAILED, failed);
    }
  });

  it('retains a blocked session in order without blocking another session', async () => {
    const legacy = { sessionId: 'legacy-session', move: 'e2e4', timestamp: 1 };
    const later = { sessionId: 'legacy-session', move: 'd2d4', timestamp: 2, sourcePgn: '' };
    const independent = { sessionId: 'sess-123', move: 'e2e4', timestamp: 3, sourcePgn: '' };
    storage.queue = [legacy, later, independent];
    vi.mocked(ApiClient.makePlaySessionMove).mockResolvedValue({
      opponent_move: 'e7e5', status: 'active',
    });
    await syncQueuedMoves();
    expect(ApiClient.getPlaySession).toHaveBeenCalledExactlyOnceWith('sess-123');
    expect(ApiClient.makePlaySessionMove).toHaveBeenCalledExactlyOnceWith('sess-123', {
      move: 'e2e4', expected_generation: 7,
    });
    expect(await getQueuedMoves()).toEqual([legacy, later]);
  });

  it('does not submit queued intent from a different history at the same FEN', async () => {
    const sourcePgn = '1. Nf3 Nf6 2. Nc3 Nc6 *';
    const canonical = '1. Nc3 Nc6 2. Nf3 Nf6 *';
    const first = new Chess();
    first.loadPgn(sourcePgn);
    const second = new Chess();
    second.loadPgn(canonical);
    expect(first.fen()).toBe(second.fen());
    const queuedMoves = [
      { sessionId: 'sess-123', move: 'e2e4', timestamp: 1, sourcePgn },
    ];
    storage.queue = queuedMoves;
    vi.mocked(ApiClient.getGame).mockResolvedValue({
      id: 'game-123', pgn: canonical, white: 'player', black: 'Stockfish', result: '*',
    });
    await syncQueuedMoves();
    expect(ApiClient.makePlaySessionMove).not.toHaveBeenCalled();
    expect(await getQueuedMoves()).toEqual(queuedMoves);
  });
});
