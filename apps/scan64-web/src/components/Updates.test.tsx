import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as platform from '../platform';
import type { UpdateStatus } from '../platform';
import { UpdatesFoot, UpdatesPanel, UpdatesProvider } from './Updates';

vi.mock('../platform', async (original) => ({
  ...await original<typeof platform>(),
  isDesktop: vi.fn(() => true), updateStatus: vi.fn(),
  checkUpdates: vi.fn(), downloadUpdate: vi.fn(), restartToUpdate: vi.fn(), setAutomaticUpdates: vi.fn(),
}));
const base: UpdateStatus = {
  state: 'idle', installedVersion: '0.6.1', availableVersion: '0.6.2', percent: null,
  lastChecked: null, automatic: true, reason: null,
  downloadUrl: 'https://github.com/Mathews-Tom/Scan64/releases/latest',
  releaseNotesUrl: 'https://github.com/Mathews-Tom/Scan64/releases/tag/v0.6.2',
};
beforeEach(() => {
  vi.mocked(platform.isDesktop).mockReturnValue(true);
  vi.mocked(platform.updateStatus).mockResolvedValue(base);
});
afterEach(() => { cleanup(); vi.clearAllMocks(); vi.useRealTimers(); });
function renderUpdates() {
  return render(<UpdatesProvider><UpdatesPanel /><UpdatesFoot /></UpdatesProvider>);
}

describe('Updates', () => {
  it.each([
    ['idle', 'Ready to check for updates.'], ['checking', 'Checking for updates…'],
    ['upToDate', 'Scan64 is up to date.'], ['available', 'Version 0.6.2 is available.'],
    ['downloading', 'Downloading 0.6.2 · 42%'],
    ['ready', 'Version 0.6.2 is ready. Restart when you are ready to update.'],
    ['installing', 'Installing update and restarting…'], ['failed', 'Update failed.'],
    ['unavailable', 'In-app updates are unavailable.'],
  ] as const)('renders %s state', async (state, text) => {
    vi.mocked(platform.updateStatus).mockResolvedValue({ ...base, state, percent: 42 });
    renderUpdates();
    expect((await screen.findAllByText(text)).length).toBeGreaterThan(0);
    expect(screen.getByText('Installed version: 0.6.1')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Release notes' })).toHaveAttribute('href', base.releaseNotesUrl);
    if (state === 'idle') expect(screen.queryByLabelText('App update')).not.toBeInTheDocument();
  });
  it('uses the persisted shell automatic setting and sends changes to the shell', async () => {
    vi.mocked(platform.updateStatus).mockResolvedValue({ ...base, automatic: false });
    renderUpdates();
    const toggle = await screen.findByRole('switch', { name: 'Download updates automatically' });
    await waitFor(() => expect(toggle).toBeEnabled());
    expect(toggle).not.toBeChecked();
    vi.mocked(platform.updateStatus).mockResolvedValue(base);
    fireEvent.click(toggle);
    await waitFor(() => expect(platform.setAutomaticUpdates).toHaveBeenCalledWith(true));
    await waitFor(() => expect(toggle).toBeChecked());
  });
  it.each([
    ['available', 'Download', 'downloadUpdate'], ['ready', 'Restart to update', 'restartToUpdate'],
    ['failed', 'Retry', 'checkUpdates'],
  ] as const)('invokes the explicit %s action', async (state, name, command) => {
    vi.mocked(platform.updateStatus).mockResolvedValue({ ...base, state, automatic: false });
    renderUpdates();
    fireEvent.click((await screen.findAllByRole('button', { name }))[0]);
    await waitFor(() => expect(platform[command]).toHaveBeenCalledOnce());
  });
  it('checks manually and shows the last check time', async () => {
    vi.mocked(platform.updateStatus).mockResolvedValue({ ...base, lastChecked: 1800000000000 });
    renderUpdates();
    await screen.findByText('Installed version: 0.6.1');
    expect(document.querySelector('time')).toHaveAttribute('dateTime', new Date(1800000000000).toISOString());
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    await waitFor(() => expect(platform.checkUpdates).toHaveBeenCalledOnce());
  });
  it.each(['Linux .deb installations cannot update in-app.', 'The install location is read-only.', 'Move Scan64 out of the translocated location.'])('shows unavailable reason: %s', async (reason) => {
    vi.mocked(platform.updateStatus).mockResolvedValue({ ...base, state: 'unavailable', reason });
    renderUpdates();
    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Download Scan64' })).toHaveAttribute('href', base.downloadUrl);
    expect(screen.getByRole('button', { name: 'Check for updates' })).toBeDisabled();
  });
  it('does not poll or invoke native commands in browser installs', () => {
    vi.mocked(platform.isDesktop).mockReturnValue(false);
    renderUpdates();
    expect(screen.getByText(platform.browserUpdateStatus.reason!)).toBeInTheDocument();
    expect(platform.updateStatus).not.toHaveBeenCalled();
    expect(screen.queryByLabelText('App update')).not.toBeInTheDocument();
  });
  it('surfaces command failures without claiming an update succeeded', async () => {
    vi.mocked(platform.checkUpdates).mockRejectedValueOnce(new Error('Network unavailable'));
    renderUpdates();
    await screen.findByText('Installed version: 0.6.1');
    fireEvent.click(screen.getByRole('button', { name: 'Check for updates' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Network unavailable');
  });
  it('clears polling on unmount, including a pending status request', async () => {
    vi.useFakeTimers();
    const { promise, resolve } = Promise.withResolvers<UpdateStatus>();
    vi.mocked(platform.updateStatus).mockReturnValue(promise);
    const view = renderUpdates();
    view.unmount();
    await act(async () => { resolve(base); });
    expect(vi.getTimerCount()).toBe(0);
  });
});
