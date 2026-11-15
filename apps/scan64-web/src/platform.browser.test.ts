import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiBase, apiFetch, initializeDesktop, isDesktop, refreshDesktopConnection, restartDesktopServer } from './platform';

afterEach(() => { vi.unstubAllGlobals(); });

describe('browser platform', () => {
  it('keeps a relative API base and does not require shell initialization', async () => {
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    expect(isDesktop()).toBe(false);
    expect(apiBase()).toBe('/v1');
    await initializeDesktop();
    await refreshDesktopConnection();
    await restartDesktopServer();
    expect(fetch).not.toHaveBeenCalled();
  });

  it('preserves the original one-argument fetch for public requests', async () => {
    const response = { ok: true };
    const fetch = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetch);
    expect(await apiFetch(`${apiBase()}/bots`)).toBe(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/bots');
  });

  it('preserves bearer authorization, request body, signal and cache options without adding a shell token', async () => {
    const response = { ok: true };
    const fetch = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetch);
    const options: RequestInit = {
      method: 'POST',
      headers: { Authorization: 'Bearer player-secret', 'Content-Type': 'application/json' },
      body: JSON.stringify({ move: 'e2e4' }),
      cache: 'no-store',
      signal: new AbortController().signal,
    };
    expect(await apiFetch(`${apiBase()}/play-sessions/session/moves`, options)).toBe(response);
    expect(fetch).toHaveBeenCalledExactlyOnceWith('/v1/play-sessions/session/moves', options);
    expect(fetch.mock.calls[0][1]).toBe(options);
    expect(new Headers(options.headers).has('X-Scan64-Shell-Token')).toBe(false);
  });

  it('preserves browser network failures', async () => {
    const error = new TypeError('Network unavailable');
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(error));
    await expect(apiFetch(`${apiBase()}/bots`)).rejects.toBe(error);
  });
});
