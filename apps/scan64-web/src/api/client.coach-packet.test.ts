import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Dynamic imports intentionally reload the platform connection singleton after each host mock.
beforeEach(() => { vi.resetModules(); localStorage.clear(); });
afterEach(() => {
  vi.unstubAllGlobals();
  vi.doUnmock('@tauri-apps/api/core');
  localStorage.clear();
});

describe('coach packet download authorization', () => {
  it('reads one blob for the requested owner without credentials in the URL or registration', async () => {
    vi.doMock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: () => false }));
    const { ApiClient } = await import('./client');
    localStorage.setItem('scan64_player_id', 'different-active-owner');
    localStorage.setItem('scan64_player_token:chosen/owner', 'chosen-secret');
    const packet = new Blob(['PK'], { type: 'application/zip' });
    const blob = vi.fn().mockResolvedValue(packet);
    const fetch = vi.fn().mockResolvedValue({ ok: true, blob });
    vi.stubGlobal('fetch', fetch);
    const signal = new AbortController().signal;
    expect(await ApiClient.getCoachPacket('chosen/owner', signal)).toBe(packet);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/players/chosen%2Fowner/progress/coach-packet', {
      headers: { Authorization: 'Bearer chosen-secret' }, signal,
    });
    expect(blob).toHaveBeenCalledTimes(1);
    expect(localStorage.getItem('scan64_player_id')).toBe('different-active-owner');
  });

  it('keeps the shell token and player bearer on the connected desktop loopback request', async () => {
    const invoke = vi.fn().mockResolvedValue({ port: 43123, token: 'shell-secret', smoke: false });
    vi.doMock('@tauri-apps/api/core', () => ({ invoke, isTauri: () => true }));
    const { initializeDesktop } = await import('../platform');
    const { ApiClient } = await import('./client');
    await initializeDesktop();
    localStorage.setItem('scan64_player_token:owner', 'player-secret');
    const packet = new Blob(['PK']);
    const fetch = vi.fn().mockResolvedValue({ ok: true, blob: async () => packet });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getCoachPacket('owner')).toBe(packet);
    expect(fetch).toHaveBeenCalledTimes(1);
    const [url, options] = fetch.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('http://127.0.0.1:43123/v1/players/owner/progress/coach-packet');
    const headers = new Headers(options.headers);
    expect(headers.get('Authorization')).toBe('Bearer player-secret');
    expect(headers.get('X-Scan64-Shell-Token')).toBe('shell-secret');
    expect(url).not.toContain('secret');
  });

  it('does not register a replacement identity or download an error response', async () => {
    vi.doMock('@tauri-apps/api/core', () => ({ invoke: vi.fn(), isTauri: () => false }));
    const { ApiClient } = await import('./client');
    const blob = vi.fn();
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 403, blob });
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.getCoachPacket('owner')).rejects.toThrow('No access token');
    expect(fetch).not.toHaveBeenCalled();
    localStorage.setItem('scan64_player_token:owner', 'wrong-secret');
    await expect(ApiClient.getCoachPacket('owner')).rejects.toMatchObject({ status: 403 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(blob).not.toHaveBeenCalled();
  });
});
