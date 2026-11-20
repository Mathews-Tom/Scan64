import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AnalysisJobRead } from './types';
import type { ApiClient as Client } from './client';

const invoke = vi.fn();
const connection = { port: 43123, token: 'shell-test-token', smoke: false };
const fetch = vi.fn<typeof globalThis.fetch>();
let ApiClient: typeof Client;

function analysisStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) { controller = value; },
    cancel,
  });
  return {
    body,
    cancel,
    response: new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }),
    push: (status: string) => controller.enqueue(new TextEncoder().encode(`${JSON.stringify({ id: 'job-1', game_id: 'game-1', status })}\n`)),
    close: () => controller.close(),
  };
}

// Reload the real transport after mocking Tauri so each test gets a fresh private shell connection.
beforeEach(async () => {
  vi.resetModules();
  fetch.mockReset();
  invoke.mockReset().mockResolvedValue(connection);
  vi.doMock('@tauri-apps/api/core', () => ({ invoke, isTauri: () => true }));
  vi.stubGlobal('fetch', fetch);
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'active-player');
  localStorage.setItem('scan64_player_token:active-player', 'player-test-token');
  localStorage.setItem('scan64_player_token:other-player', 'other-test-token');
  const platform = await import('../platform');
  await platform.initializeDesktop();
  ({ ApiClient } = await import('./client'));
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.doUnmock('@tauri-apps/api/core');
  localStorage.clear();
});

describe('analysis job events', () => {
  it.each(['completed', 'failed'])('keeps bearer and shell headers on the real platform transport through %s', async status => {
    const stream = analysisStream();
    fetch.mockResolvedValueOnce(stream.response);
    const firstUpdate = Promise.withResolvers<void>();
    const updates = vi.fn((_job: AnalysisJobRead) => firstUpdate.resolve());
    const controller = new AbortController();
    const pending = ApiClient.watchAnalysisJob('job-1', updates, controller.signal);
    stream.push('pending');
    await firstUpdate.promise;
    expect(updates).toHaveBeenCalledExactlyOnceWith({ id: 'job-1', game_id: 'game-1', status: 'pending' });
    expect(stream.cancel).not.toHaveBeenCalled();
    expect(fetch).toHaveBeenCalledOnce();
    const [url, options] = fetch.mock.calls[0];
    expect(url).toBe('http://127.0.0.1:43123/v1/analysis-jobs/job-1/events');
    expect(new URL(String(url)).search).toBe('');
    expect(options?.signal).toBe(controller.signal);
    const headers = new Headers(options?.headers);
    expect(headers.get('Authorization')).toBe('Bearer player-test-token');
    expect(headers.get('X-Scan64-Shell-Token')).toBe(connection.token);

    stream.push('running');
    stream.push(status);
    await expect(pending).resolves.toEqual({ id: 'job-1', game_id: 'game-1', status });
    expect(updates.mock.calls.map(([job]) => job.status)).toEqual(['pending', 'running', status]);
    expect(fetch).toHaveBeenCalledOnce();
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
    expect(localStorage.getItem('scan64_player_token:active-player')).toBe('player-test-token');
  });

  it.each(['identity', 'token'])('refuses a subscription without the active player %s before making a request', async missing => {
    localStorage.removeItem(missing === 'identity' ? 'scan64_player_id' : 'scan64_player_token:active-player');
    const updates = vi.fn();
    await expect(ApiClient.watchAnalysisJob('job-1', updates, new AbortController().signal)).rejects.toMatchObject({ status: 401 });
    expect(fetch).not.toHaveBeenCalled();
    expect(updates).not.toHaveBeenCalled();
  });

  it.each([401, 404])('preserves HTTP %s and never reconnects or replaces the stored authorization', async status => {
    vi.useFakeTimers();
    fetch.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Not available' }), { status }));
    const updates = vi.fn();
    await expect(ApiClient.watchAnalysisJob('job-1', updates, new AbortController().signal)).rejects.toMatchObject({ status });
    await vi.advanceTimersByTimeAsync(60_000);
    expect(fetch).toHaveBeenCalledOnce();
    expect(updates).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
    expect(localStorage.getItem('scan64_player_id')).toBe('active-player');
    expect(localStorage.getItem('scan64_player_token:active-player')).toBe('player-test-token');
  });

  it('reports an interrupted job instead of turning EOF into completion or retrying', async () => {
    vi.useFakeTimers();
    const stream = analysisStream();
    fetch.mockResolvedValueOnce(stream.response);
    const updates = vi.fn();
    const pending = ApiClient.watchAnalysisJob('job-1', updates, new AbortController().signal);
    stream.push('running');
    stream.close();
    await expect(pending).rejects.toThrow(/disconnected before completion/i);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(updates).toHaveBeenCalledExactlyOnceWith({ id: 'job-1', game_id: 'game-1', status: 'running' });
    expect(fetch).toHaveBeenCalledOnce();
    expect(vi.getTimerCount()).toBe(0);
    expect(stream.body.locked).toBe(false);
  });

  it('rejects an unknown job state without publishing it to the screen', async () => {
    const stream = analysisStream();
    fetch.mockResolvedValueOnce(stream.response);
    const updates = vi.fn();
    const pending = ApiClient.watchAnalysisJob('job-1', updates, new AbortController().signal);
    stream.push('not-a-status');
    await expect(pending).rejects.toThrow(/malformed/i);
    expect(updates).not.toHaveBeenCalled();
    expect(stream.cancel).toHaveBeenCalledOnce();
    expect(stream.body.locked).toBe(false);
  });

  it('passes disposal to the transport and cancels a reader waiting for the next update', async () => {
    const stream = analysisStream();
    fetch.mockResolvedValueOnce(stream.response);
    const received = Promise.withResolvers<void>();
    const updates = vi.fn((_job: AnalysisJobRead) => received.resolve());
    const controller = new AbortController();
    const pending = ApiClient.watchAnalysisJob('job-1', updates, controller.signal);
    stream.push('running');
    await received.promise;
    const aborted = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    controller.abort();
    await aborted;
    expect(fetch.mock.calls[0][1]?.signal).toBe(controller.signal);
    expect(stream.cancel).toHaveBeenCalledExactlyOnceWith(controller.signal.reason);
    expect(stream.body.locked).toBe(false);
    expect(updates).toHaveBeenCalledOnce();
    expect(fetch).toHaveBeenCalledOnce();
  });
});
