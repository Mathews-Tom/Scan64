import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

beforeEach(() => {
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'secret');
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('owned game review client', () => {
  it('reads review with the active owner bearer token', async () => {
    const response = { game_id: 'game-1', moves: [] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getGameReview('game-1')).toEqual(response);
    expect(fetch).toHaveBeenCalledWith('/v1/games/game-1/review', { headers: { Authorization: 'Bearer secret' } });
  });

  it('starts backfill only via explicit owned POST', async () => {
    const response = { game_id: 'game-1', moves: [], key_moments: [], session_id: null };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.backfillGameReview('game-1')).toEqual(response);
    expect(fetch).toHaveBeenCalledWith('/v1/games/game-1/review/backfill', { method: 'POST', headers: { Authorization: 'Bearer secret' } });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it('preserves shared not-found errors and backfill details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'Not found' }) }));
    await expect(ApiClient.getGameReview('foreign')).rejects.toMatchObject({ status: 404 });
    await expect(ApiClient.backfillGameReview('foreign')).rejects.toBeInstanceOf(ApiRequestError);
    await expect(ApiClient.backfillGameReview('foreign')).rejects.toThrow('Not found');
  });

  it('requires active authorization without issuing a request', async () => {
    localStorage.clear();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.getGameReview('game-1')).rejects.toMatchObject({ status: 401 });
    await expect(ApiClient.backfillGameReview('game-1')).rejects.toMatchObject({ status: 401 });
    expect(fetch).not.toHaveBeenCalled();
  });
});
