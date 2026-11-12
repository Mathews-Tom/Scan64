import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe('getPositionInsight', () => {
  it('posts the session source with its player bearer token', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    const result = { step: 3, fen: 'position', candidates: [], visualizations: [] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => result });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getPositionInsight({ player_id: 'p', play_session_id: 's', step: 3 })).toEqual(result);
    expect(fetch).toHaveBeenCalledWith('/v1/insights/position', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer secret' },
      body: JSON.stringify({ player_id: 'p', play_session_id: 's', step: 3 }),
    });
  });
  it('surfaces endpoint failures', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: 'Busy', json: async () => ({ detail: 'Hint engine is disabled' }) }));
    await expect(ApiClient.getPositionInsight({ player_id: 'p', fen: 'position', step: 1 })).rejects.toBeInstanceOf(ApiRequestError);
    await expect(ApiClient.getPositionInsight({ player_id: 'p', fen: 'position', step: 2 })).rejects.toThrow('Hint engine is disabled');
  });
});
