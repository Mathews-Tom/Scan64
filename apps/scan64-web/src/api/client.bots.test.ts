import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('bot ladder API', () => {
  it('loads the server catalogue without manufacturing Elo or level names', async () => {
    const bots = Array.from({ length: 8 }, (_, index) => ({
      level: index + 1, name: `Server bot ${index + 1}`, description: 'Server description',
      ...(index >= 4 ? { elo: 1320 + index * 100 } : {}),
    }));
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => bots });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getBots()).toEqual(bots);
    expect(fetch).toHaveBeenCalledWith('/v1/bots');
    expect(bots[0]).not.toHaveProperty('elo');
  });

  it('posts takeback with the player bearer token and returns authoritative session', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    const session = { id: 'session-1', game_id: 'game-1', player_color: 'black', status: 'active' };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => session });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.takebackPlaySession('session-1')).toEqual(session);
    expect(fetch).toHaveBeenCalledWith('/v1/play-sessions/session-1/takeback', {
      method: 'POST', headers: { Authorization: 'Bearer token-1' },
    });
  });

  it('preserves a takeback refusal status', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: 'Conflict' }));
    await expect(ApiClient.takebackPlaySession('session-1')).rejects.toBeInstanceOf(ApiRequestError);
  });
});
