import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe('getPuzzleDaily', () => {
  it('reads daily practice with the active bearer token without starting a run', async () => {
    localStorage.setItem('scan64_player_id', 'p');
    localStorage.setItem('scan64_player_token:p', 'secret');
    const daily = {
      utc_day: '2026-11-13', rating: 1200, rated_count: 0, provisional: true,
      overdue_count: 0, total: 10, completed: 0, remaining: 10,
      actual_mix: { due: 4, weak: 5, exploration: 1 }, message: 'Ready',
    };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => daily });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getPuzzleDaily()).toEqual(daily);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/puzzles/daily', {
      method: 'GET', headers: { Authorization: 'Bearer secret' },
    });
  });

  it('surfaces daily endpoint failures', async () => {
    localStorage.setItem('scan64_player_id', 'p');
    localStorage.setItem('scan64_player_token:p', 'secret');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({
      ok: false, status: 503, statusText: 'Busy', json: async () => ({ detail: 'Practice unavailable' }),
    }));
    await expect(ApiClient.getPuzzleDaily()).rejects.toBeInstanceOf(ApiRequestError);
  });
});
