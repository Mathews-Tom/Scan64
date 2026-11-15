import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Reload the adapter after installing each native/browser module boundary mock.

const invoke = vi.fn();
let desktop = true;
beforeEach(() => {
  vi.resetModules();
  desktop = true;
  invoke.mockReset().mockResolvedValue(undefined);
  vi.doMock('@tauri-apps/api/core', () => ({ invoke, isTauri: () => desktop }));
});
afterEach(() => { vi.doUnmock('@tauri-apps/api/core'); });

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
    await platform.checkUpdates();
    await platform.downloadUpdate();
    await platform.restartToUpdate();
    await platform.setAutomaticUpdates(false);
    await platform.setAutomaticUpdates(true);
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
    await platform.checkUpdates();
    await platform.downloadUpdate();
    await platform.restartToUpdate();
    await platform.setAutomaticUpdates(true);
    await platform.openReleaseUrl(platform.browserUpdateStatus.releaseNotesUrl);
    expect(invoke).not.toHaveBeenCalled();
  });
});
