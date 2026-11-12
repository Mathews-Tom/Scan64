import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe('resignPlaySession', () => {
  it('posts with the active player bearer token and returns the terminal session', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_active_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    const session = { id: 'session-1', game_id: 'game-1', status: 'completed' };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => session });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.resignPlaySession('session-1')).toEqual(session);
    expect(fetch).toHaveBeenCalledWith('/v1/play-sessions/session-1/resign', {
      method: 'POST', headers: { Authorization: 'Bearer token-1' },
    });
  });
  it('does not treat a refused resignation as completion', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: 'Conflict' }));
    await expect(ApiClient.resignPlaySession('session-1')).rejects.toBeInstanceOf(ApiRequestError);
  });
});
