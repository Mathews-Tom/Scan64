import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });

describe('progress reset authorization', () => {
  it('resets only the selected identity without registering or changing it', async () => {
    localStorage.setItem('scan64_player_id', 'selected');
    localStorage.setItem('scan64_player_token:selected', 'selected-token');
    localStorage.setItem('scan64_player_token:other', 'other-token');
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({
      player_id: 'selected', reset_at: '2026-11-16T00:00:00Z', cleared: { puzzle_runs: 2 },
    }) });
    vi.stubGlobal('fetch', fetch);
    await ApiClient.resetProgress('RESET PROGRESS');
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/selected/progress/reset', {
      method: 'POST', headers: { Authorization: 'Bearer selected-token', 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirmation: 'RESET PROGRESS' }),
    });
    expect(localStorage.getItem('scan64_player_id')).toBe('selected');
    expect(localStorage.getItem('scan64_player_token:other')).toBe('other-token');
  });

  it('cannot register a replacement player or send deletion when authorization is missing', async () => {
    localStorage.setItem('scan64_player_id', 'selected');
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.resetProgress('RESET PROGRESS')).rejects.toThrow('No access token');
    expect(fetch).not.toHaveBeenCalled();
    expect(localStorage.getItem('scan64_player_id')).toBe('selected');
  });

  it('surfaces refusal without retrying a destructive request', async () => {
    localStorage.setItem('scan64_player_id', 'selected');
    localStorage.setItem('scan64_player_token:selected', 'selected-token');
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 409,
      json: async () => ({ detail: 'Progress changed. No reset was committed.' }) });
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.resetProgress('RESET PROGRESS')).rejects.toThrow('No reset was committed');
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
