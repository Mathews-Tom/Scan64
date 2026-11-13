import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

beforeEach(() => {
  localStorage.setItem('scan64_player_id', 'owner');
  localStorage.setItem('scan64_player_token:owner', 'owner-secret');
  localStorage.setItem('scan64_player_token:other/owner', 'other-secret');
});
afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('owned habit client', () => {
  it('reads a habit summary with the requested owner token without a write', async () => {
    const response = { utc_day: '2026-11-13', goal: 3 };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getPlayerHabit('other/owner')).toEqual(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/other%2Fowner/habit', {
      method: 'GET', headers: { Authorization: 'Bearer other-secret' },
    });
  });

  it('posts a next-day goal with authenticated JSON', async () => {
    const response = { goal: 3, pending_goal: 5, pending_effective_day: '2026-11-14' };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.updateHabitGoal('owner', 5)).toEqual(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/owner/habit/goal', {
      method: 'POST', headers: { Authorization: 'Bearer owner-secret', 'Content-Type': 'application/json' },
      body: JSON.stringify({ goal: 5 }),
    });
  });

  it('starts a target only with the selected diagnosis code', async () => {
    const response = { targets: [{ id: 'target', code: 'board_awareness.hanging_piece' }] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.startHabitTarget('owner', 'board_awareness.hanging_piece')).toEqual(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/owner/habit/targets', {
      method: 'POST', headers: { Authorization: 'Bearer owner-secret', 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: 'board_awareness.hanging_piece' }),
    });
  });

  it('refreshes a target with an explicit owned empty-object POST and encoded target id', async () => {
    const response = { targets: [{ id: 'target/one', invalidity_reason: 'Evidence withdrawn' }] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.refreshHabitTarget('owner', 'target/one')).toEqual(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/owner/habit/targets/target%2Fone/refresh', {
      method: 'POST', headers: { Authorization: 'Bearer owner-secret', 'Content-Type': 'application/json' }, body: '{}',
    });
  });

  it('preserves authorization and invalid-pattern failures rather than falling back', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: 'Not Found', json: async () => ({ detail: 'Target not found' }) });
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.refreshHabitTarget('owner', 'foreign-target')).rejects.toMatchObject({ status: 404 });
    await expect(ApiClient.startHabitTarget('owner', 'not-recurring')).rejects.toThrow('Target not found');
    await expect(ApiClient.getPlayerHabit('owner')).rejects.toBeInstanceOf(ApiRequestError);
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it('keeps the HTTP status when an error body is not JSON', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: 'Unavailable', json: async () => { throw new Error('Not JSON'); } }));
    await expect(ApiClient.updateHabitGoal('owner', 3)).rejects.toMatchObject({ status: 503, message: 'Habit request failed: Unavailable' });
  });

  it('never registers or issues a request without the requested owner credential', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.getPlayerHabit('unknown')).rejects.toThrow('No access token');
    await expect(ApiClient.updateHabitGoal('unknown', 3)).rejects.toThrow('No access token');
    await expect(ApiClient.startHabitTarget('unknown', 'fork')).rejects.toThrow('No access token');
    await expect(ApiClient.refreshHabitTarget('unknown', 'target')).rejects.toThrow('No access token');
    expect(fetch).not.toHaveBeenCalled();
  });
});
