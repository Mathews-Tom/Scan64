import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.fn();
const server = { port: 43123, token: 'per-launch-secret', smoke: false };

// Dynamic imports below intentionally reload the module's private connection state for each test.
beforeEach(() => {
  vi.resetModules();
  invoke.mockReset().mockResolvedValue(server);
  vi.doMock('@tauri-apps/api/core', () => ({ invoke, isTauri: () => true }));
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.doUnmock('@tauri-apps/api/core');
});

describe('desktop platform', () => {
  it('returns the native selected path or cancellation and preserves picker errors', async () => {
    const { choosePuzzleFile } = await import('./platform');
    invoke.mockResolvedValueOnce('/tmp/puzzles.csv.zst');
    expect(await choosePuzzleFile()).toBe('/tmp/puzzles.csv.zst');
    expect(invoke).toHaveBeenLastCalledWith('choose_puzzle_file');
    invoke.mockResolvedValueOnce(null);
    expect(await choosePuzzleFile()).toBeNull();
    invoke.mockRejectedValueOnce('Puzzle file is not readable');
    await expect(choosePuzzleFile()).rejects.toThrow('Puzzle file is not readable');
  });

  it('requires the shell connection before constructing an API URL', async () => {
    const platform = await import('./platform');
    expect(platform.isDesktop()).toBe(true);
    expect(() => platform.apiBase()).toThrow('not connected');
    await platform.initializeDesktop();
    expect(invoke).toHaveBeenCalledExactlyOnceWith('desktop_connection');
    expect(platform.apiBase()).toBe('http://127.0.0.1:43123/v1');
  });

  it('adds the shell header without replacing player authorization or mutating caller headers', async () => {
    const platform = await import('./platform');
    await platform.initializeDesktop();
    const response = { ok: true };
    const fetch = vi.fn().mockResolvedValue(response);
    vi.stubGlobal('fetch', fetch);
    const headers = new Headers({ Authorization: 'Bearer player-secret', 'Content-Type': 'application/json' });
    const signal = new AbortController().signal;
    const url = `${platform.apiBase()}/games`;
    expect(await platform.apiFetch(url, { method: 'POST', headers, body: '{}', signal })).toBe(response);
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(fetch.mock.calls[0][0]).toBe(url);
    expect(options).toMatchObject({ method: 'POST', body: '{}', signal });
    expect(new Headers(options.headers).get('Authorization')).toBe('Bearer player-secret');
    expect(new Headers(options.headers).get('X-Scan64-Shell-Token')).toBe(server.token);
    expect(headers.has('X-Scan64-Shell-Token')).toBe(false);
  });

  it('never sends the shell token to another host, port or non-API URL', async () => {
    const platform = await import('./platform');
    await platform.initializeDesktop();
    const fetch = vi.fn();
    vi.stubGlobal('fetch', fetch);
    for (const url of ['https://example.com/v1/bots', 'http://127.0.0.1:43124/v1/bots', 'http://127.0.0.1:43123/v10/bots']) {
      expect(() => platform.apiFetch(url)).toThrow('connected loopback server');
    }
    expect(fetch).not.toHaveBeenCalled();
  });

  it('updates the connection after restart while leaving the page origin and stored identity alone', async () => {
    const platform = await import('./platform');
    await platform.initializeDesktop();
    const origin = window.location.origin;
    localStorage.setItem('scan64_player_id', 'existing-player');
    invoke.mockResolvedValueOnce({ ...server, port: 43210, token: 'replacement-secret' });
    await platform.restartDesktopServer();
    expect(invoke).toHaveBeenLastCalledWith('restart_server');
    expect(platform.apiBase()).toBe('http://127.0.0.1:43210/v1');
    const fetch = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal('fetch', fetch);
    await platform.apiFetch(`${platform.apiBase()}/bots`);
    expect(new Headers(fetch.mock.calls[0][1].headers).get('X-Scan64-Shell-Token')).toBe('replacement-secret');
    expect(window.location.origin).toBe(origin);
    expect(localStorage.getItem('scan64_player_id')).toBe('existing-player');
    localStorage.removeItem('scan64_player_id');
  });

  it('preserves the log path on startup and later server failures and invalidates the old connection', async () => {
    const platform = await import('./platform');
    const failure = 'Server exited. Log: /tmp/scan64/server.log';
    invoke.mockRejectedValueOnce(failure);
    await expect(platform.initializeDesktop()).rejects.toBe(failure);
    await platform.initializeDesktop();
    invoke.mockRejectedValueOnce(failure);
    await expect(platform.refreshDesktopConnection()).rejects.toBe(failure);
    expect(() => platform.apiBase()).toThrow('not connected');
  });

  it('records a successful tokened create-player request from the bundled page origin', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true });
    vi.stubGlobal('window', { location: { protocol: 'tauri:', host: 'localhost', origin: 'null' } });
    const json = vi.fn().mockResolvedValue({ id: 'smoke-player', access_token: 'player-secret' });
    const fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json });
    vi.stubGlobal('fetch', fetch);
    await platform.initializeDesktop();
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(fetch.mock.calls[0][0]).toBe('http://127.0.0.1:43123/v1/players');
    const options = fetch.mock.calls[0][1] as RequestInit;
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body as string)).toMatchObject({ display_name: 'Desktop smoke' });
    expect(new Headers(options.headers).get('X-Scan64-Shell-Token')).toBe(server.token);
    expect(json).toHaveBeenCalledOnce();
    expect(invoke).toHaveBeenLastCalledWith('smoke_complete', { origin: 'tauri://localhost', path: '/v1/players', status: 200 });
  });

  it('prepares the ordinary board route only in explicit board-smoke mode', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true, smoke_board: true });
    const replaceState = vi.spyOn(window.history, 'replaceState').mockImplementation(() => {});
    const fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => ({ id: 'board-smoke-player', access_token: 'player-secret' }),
    });
    vi.stubGlobal('fetch', fetch);
    try {
      await platform.initializeDesktop();
      expect(fetch).toHaveBeenCalledTimes(2);
      expect(replaceState).toHaveBeenCalledWith({}, '', '/analysis');
      expect(localStorage.getItem('scan64_player_id')).not.toBeNull();
      expect(invoke).toHaveBeenLastCalledWith('smoke_complete', {
        origin: `${window.location.protocol}//${window.location.host}`,
        path: '/v1/players', status: 200,
      });
    } finally {
      replaceState.mockRestore();
      localStorage.clear();
    }
  });

  it('plays a bundled-engine move before recording packaged smoke success', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true, smoke_engine: true });
    const fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: async () => ({ id: 'engine-smoke-player', access_token: 'player-secret' }),
      })
      .mockResolvedValueOnce({
        ok: true, status: 200, json: async () => ({ opponent_move: 'e2e4' }),
      });
    vi.stubGlobal('fetch', fetch);
    await platform.initializeDesktop();
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch.mock.calls[1][0]).toBe('http://127.0.0.1:43123/v1/play-sessions');
    const options = fetch.mock.calls[1][1] as RequestInit;
    expect(new Headers(options.headers).get('Authorization')).toBe('Bearer player-secret');
    expect(new Headers(options.headers).get('X-Scan64-Shell-Token')).toBe(server.token);
    expect(JSON.parse(options.body as string)).toMatchObject({
      player_id: 'engine-smoke-player', player_color: 'black',
    });
    expect(invoke).toHaveBeenLastCalledWith('smoke_complete', expect.anything());
    expect(JSON.stringify(invoke.mock.calls)).not.toContain('player-secret');
  });

  it('does not certify packaged smoke without a bot move', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true, smoke_engine: true });
    const fetch = vi.fn()
      .mockResolvedValueOnce({
        ok: true, status: 200,
        json: async () => ({ id: 'engine-smoke-player', access_token: 'player-secret' }),
      })
      .mockResolvedValueOnce({
        ok: true, status: 200, json: async () => ({ opponent_move: null }),
      });
    vi.stubGlobal('fetch', fetch);
    await expect(platform.initializeDesktop()).rejects.toThrow('bot move');
    expect(invoke).not.toHaveBeenCalledWith('smoke_complete', expect.anything());
  });

  it('does not record smoke success after an unsuccessful API response', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    await expect(platform.initializeDesktop()).rejects.toThrow('HTTP 401');
    expect(invoke).not.toHaveBeenCalledWith('smoke_complete', expect.anything());
    expect(invoke).toHaveBeenLastCalledWith('smoke_failed', {
      origin: `${window.location.protocol}//${window.location.host}`,
      reason: 'Error: Desktop smoke request failed: HTTP 401',
    });
  });

  it('redacts the shell credential from smoke failure diagnostics', async () => {
    const platform = await import('./platform');
    invoke.mockResolvedValueOnce({ ...server, smoke: true });
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error(`Blocked ${server.token}`)));
    await expect(platform.initializeDesktop()).rejects.toThrow('Blocked');
    expect(invoke).toHaveBeenLastCalledWith('smoke_failed', {
      origin: `${window.location.protocol}//${window.location.host}`,
      reason: 'Error: Blocked [redacted]',
    });
  });
});
