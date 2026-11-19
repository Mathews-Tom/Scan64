import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

beforeEach(() => {
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'token-1');
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('timed play API', () => {
  it('sends numeric time control rather than client remaining time', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ id: 'session-1' }) });
    vi.stubGlobal('fetch', fetch);
    await ApiClient.createPlaySession({ player_id: 'player-1', clock_config: { initial_seconds: 180, increment_seconds: 2 } });
    expect(fetch).toHaveBeenCalledWith('/v1/play-sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ player_id: 'player-1', clock_config: { initial_seconds: 180, increment_seconds: 2 } }),
    });
  });

  it('sends the exact clock version on human and owned opponent commands', async () => {
    const snapshot = { id: 'session-1', clock: { version: 9 } };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => snapshot });
    vi.stubGlobal('fetch', fetch);
    await ApiClient.makePlaySessionMove('session-1', { move: 'e2e4', expected_clock_version: 7 });
    expect(fetch).toHaveBeenLastCalledWith('/v1/play-sessions/session-1/moves', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ move: 'e2e4', expected_clock_version: 7 }),
    });
    expect(await ApiClient.resumePlaySessionOpponentMove('session-1', 8)).toEqual(snapshot);
    expect(fetch).toHaveBeenLastCalledWith('/v1/play-sessions/session-1/opponent-move', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ expected_clock_version: 8 }),
    });
  });

  it('preserves explicit stale clock conflicts for both commands', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 409, statusText: 'Conflict' }));
    for (const command of [
      () => ApiClient.makePlaySessionMove('session-1', { move: 'e2e4', expected_clock_version: 1 }),
      () => ApiClient.resumePlaySessionOpponentMove('session-1', 1),
    ]) {
      const request = command();
      await expect(request).rejects.toBeInstanceOf(ApiRequestError);
      await expect(request).rejects.toMatchObject({ status: 409 });
    }
  });
});
