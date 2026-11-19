import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateStatus } from './platform';

// Reload the adapter after installing each native/browser module boundary mock.

const invoke = vi.fn();
const listen = vi.fn();
const unlisten = vi.fn();
let desktop = true;
beforeEach(() => {
  vi.resetModules();
  desktop = true;
  invoke.mockReset().mockResolvedValue(undefined);
  unlisten.mockReset();
  listen.mockReset().mockResolvedValue(unlisten);
  vi.doMock('@tauri-apps/api/core', () => ({ invoke, isTauri: () => desktop }));
  vi.doMock('@tauri-apps/api/event', () => ({ listen }));
});
afterEach(() => { vi.doUnmock('@tauri-apps/api/core'); vi.doUnmock('@tauri-apps/api/event'); });

describe('update platform adapter', () => {
  it('passes the shell status through without inventing client state', async () => {
    const platform = await import('./platform');
    const status = { ...platform.browserUpdateStatus, state: 'ready', installedVersion: '0.6.1', availableVersion: '0.6.2', automatic: true };
    invoke.mockResolvedValue(status);
    expect(await platform.updateStatus()).toBe(status);
    expect(invoke).toHaveBeenCalledWith('update_status');
  });
  it('uses only the shared native commands, including both automatic preferences', async () => {
    const platform = await import('./platform');
    const status = { ...platform.browserUpdateStatus, revision: 7 };
    invoke.mockResolvedValue(status);
    expect(await platform.checkUpdates()).toBe(status);
    expect(await platform.downloadUpdate()).toBe(status);
    expect(await platform.restartToUpdate()).toBe(status);
    expect(await platform.setAutomaticUpdates(false)).toBe(status);
    expect(await platform.setAutomaticUpdates(true)).toBe(status);
    expect(invoke.mock.calls).toEqual([
      ['check_updates'], ['download_update'], ['restart_to_update'],
      ['set_automatic_updates', { enabled: false }], ['set_automatic_updates', { enabled: true }],
    ]);
  });
  it('opens a user-selected release URL only through the native command', async () => {
    const platform = await import('./platform');
    expect(invoke).not.toHaveBeenCalled();
    const url = 'https://github.com/Mathews-Tom/Scan64/releases/tag/v0.6.2';
    await platform.openReleaseUrl(url);
    expect(invoke).toHaveBeenCalledExactlyOnceWith('open_release_url', { url });
  });
  it('preserves release link rejection for the UI', async () => {
    const platform = await import('./platform');
    invoke.mockRejectedValue(new Error('Only Scan64 GitHub release links can be opened'));
    await expect(platform.openReleaseUrl('https://example.com')).rejects.toThrow('Only Scan64 GitHub release links can be opened');
  });
  it('preserves native failures for the UI', async () => {
    const platform = await import('./platform');
    invoke.mockRejectedValue(new Error('Signature verification failed'));
    await expect(platform.downloadUpdate()).rejects.toThrow('Signature verification failed');
  });
  it('never invokes native update commands from a browser install', async () => {
    desktop = false;
    const platform = await import('./platform');
    expect(await platform.updateStatus()).toEqual(platform.browserUpdateStatus);
    expect(platform.browserUpdateStatus.state).toBe('unavailable');
    expect(await platform.checkUpdates()).toBe(platform.browserUpdateStatus);
    expect(await platform.downloadUpdate()).toBe(platform.browserUpdateStatus);
    expect(await platform.restartToUpdate()).toBe(platform.browserUpdateStatus);
    expect(await platform.setAutomaticUpdates(true)).toBe(platform.browserUpdateStatus);
    await platform.openReleaseUrl(platform.browserUpdateStatus.releaseNotesUrl);
    expect(invoke).not.toHaveBeenCalled();
    expect(listen).not.toHaveBeenCalled();
  });
  it('installs the native listener before the snapshot and rejects late or duplicate revisions', async () => {
    const platform = await import('./platform');
    const snapshot = Promise.withResolvers<UpdateStatus>();
    invoke.mockReturnValue(snapshot.promise);
    const onStatus = vi.fn();
    const abort = new AbortController();
    const subscription = platform.subscribeUpdateStatus(onStatus, abort.signal);
    expect(listen).toHaveBeenCalledWith('scan64-update-status', expect.any(Function));
    expect(invoke).not.toHaveBeenCalled();
    await Promise.resolve();
    expect(invoke).toHaveBeenCalledExactlyOnceWith('update_status');
    const event = (revision: number) => listen.mock.calls[0][1]({ payload: { ...platform.browserUpdateStatus, revision } });
    event(3);
    snapshot.resolve({ ...platform.browserUpdateStatus, revision: 1 });
    const dispose = await subscription;
    event(2);
    event(3);
    event(4);
    expect(onStatus.mock.calls.map(([status]) => status.revision)).toEqual([3, 4]);
    dispose();
    dispose();
    abort.abort();
    event(5);
    expect(unlisten).toHaveBeenCalledOnce();
    expect(onStatus).toHaveBeenCalledTimes(2);
  });
  it('delivers the current terminal snapshot and later failure events without polling', async () => {
    const platform = await import('./platform');
    const status = { ...platform.browserUpdateStatus, revision: 1, state: 'upToDate' as const };
    invoke.mockResolvedValue(status);
    const onStatus = vi.fn();
    const dispose = await platform.subscribeUpdateStatus(onStatus, new AbortController().signal);
    expect(onStatus).toHaveBeenCalledExactlyOnceWith(status);
    const failed = { ...status, revision: 2, state: 'failed' as const, reason: 'Connection failed' };
    listen.mock.calls[0][1]({ payload: failed });
    expect(onStatus).toHaveBeenLastCalledWith(failed);
    expect(invoke).toHaveBeenCalledOnce();
    dispose();
  });
  it('does nothing when aborted before subscription', async () => {
    const platform = await import('./platform');
    const abort = new AbortController();
    abort.abort();
    const onStatus = vi.fn();
    const dispose = await platform.subscribeUpdateStatus(onStatus, abort.signal);
    dispose();
    expect(listen).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
    expect(onStatus).not.toHaveBeenCalled();
  });
  it('unlistens exactly once when aborted before listener installation resolves', async () => {
    const platform = await import('./platform');
    const installed = Promise.withResolvers<() => void>();
    listen.mockReturnValue(installed.promise);
    const abort = new AbortController();
    const onStatus = vi.fn();
    const subscription = platform.subscribeUpdateStatus(onStatus, abort.signal);
    abort.abort();
    listen.mock.calls[0][1]({ payload: { ...platform.browserUpdateStatus, revision: 1 } });
    installed.resolve(unlisten);
    const dispose = await subscription;
    dispose();
    expect(unlisten).toHaveBeenCalledOnce();
    expect(onStatus).not.toHaveBeenCalled();
    expect(invoke).not.toHaveBeenCalled();
  });
  it('disposes immediately during a pending snapshot and suppresses its completion', async () => {
    const platform = await import('./platform');
    const snapshot = Promise.withResolvers<UpdateStatus>();
    invoke.mockReturnValue(snapshot.promise);
    const abort = new AbortController();
    const remove = vi.spyOn(abort.signal, 'removeEventListener');
    const onStatus = vi.fn();
    const subscription = platform.subscribeUpdateStatus(onStatus, abort.signal);
    await Promise.resolve();
    abort.abort();
    expect(unlisten).toHaveBeenCalledOnce();
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    snapshot.resolve(platform.browserUpdateStatus);
    (await subscription)();
    expect(onStatus).not.toHaveBeenCalled();
    expect(unlisten).toHaveBeenCalledOnce();
  });
  it.each(['listen', 'snapshot'])('surfaces %s startup failures and cleans installed resources', async (stage) => {
    const platform = await import('./platform');
    const abort = new AbortController();
    const remove = vi.spyOn(abort.signal, 'removeEventListener');
    if (stage === 'listen') listen.mockRejectedValueOnce(new Error('Listener unavailable'));
    else invoke.mockRejectedValueOnce(new Error('Snapshot unavailable'));
    await expect(platform.subscribeUpdateStatus(vi.fn(), abort.signal)).rejects.toThrow('unavailable');
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(unlisten).toHaveBeenCalledTimes(stage === 'listen' ? 0 : 1);
    abort.abort();
    expect(unlisten).toHaveBeenCalledTimes(stage === 'listen' ? 0 : 1);
  });
  it('delivers browser unavailability once without touching native APIs', async () => {
    desktop = false;
    const platform = await import('./platform');
    const onStatus = vi.fn();
    const abort = new AbortController();
    const dispose = await platform.subscribeUpdateStatus(onStatus, abort.signal);
    expect(platform.browserUpdateStatus.revision).toBe(0);
    expect(onStatus).toHaveBeenCalledExactlyOnceWith(platform.browserUpdateStatus);
    dispose();
    abort.abort();
    const cancelled = new AbortController();
    cancelled.abort();
    await platform.subscribeUpdateStatus(onStatus, cancelled.signal);
    expect(onStatus).toHaveBeenCalledOnce();
    expect(invoke).not.toHaveBeenCalled();
    expect(listen).not.toHaveBeenCalled();
  });
});
