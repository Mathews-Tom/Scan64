import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { PlayMoveResponse } from './types';
import { ApiClient } from './client';
import { syncQueuedMoves } from './offlineQueue';
import { get, set } from 'idb-keyval';

vi.mock('./client', () => ({
  ApiClient: {
    makePlaySessionMove: vi.fn(),
  },
}));

vi.mock('idb-keyval', () => ({
  get: vi.fn(),
  set: vi.fn(),
}));

describe('syncQueuedMoves', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(get).mockResolvedValue([
      { sessionId: 'sess-123', move: 'e2e4', timestamp: 1 },
    ]);
    vi.mocked(set).mockResolvedValue(undefined);
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
    });
  });
});
