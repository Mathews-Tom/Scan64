import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { DesktopUpdateBanner } from './DesktopUpdateBanner';

vi.mock('../api/client', () => ({ ApiClient: { getAppInfo: vi.fn(), getAppUpdate: vi.fn() } }));

async function settle() {
  await act(async () => { await Promise.resolve(); });
}

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(ApiClient.getAppInfo).mockResolvedValue({ distribution: 'desktop' });
  vi.mocked(ApiClient.getAppUpdate).mockResolvedValue({ status: 'ready', version: '0.5.1' });
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
});

describe('desktop update notification', () => {
  it('does not request update state for source sessions', async () => {
    vi.mocked(ApiClient.getAppInfo).mockResolvedValue({ distribution: 'source' });
    render(<DesktopUpdateBanner />);
    await settle();
    expect(ApiClient.getAppUpdate).not.toHaveBeenCalled();
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('only shows ready updates and lets the user dismiss a version', async () => {
    vi.mocked(ApiClient.getAppUpdate).mockResolvedValueOnce({ status: 'downloading', version: '0.5.1' });
    render(<DesktopUpdateBanner />);
    await settle();
    expect(screen.queryByRole('status')).toBeNull();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByRole('status').textContent).toBe('Scan64 0.5.1 is ready — restart from the Scan64 window');
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss update notification' }));
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.queryByRole('status')).toBeNull();
    vi.mocked(ApiClient.getAppUpdate).mockResolvedValue({ status: 'ready', version: '0.5.2' });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(screen.getByRole('status').textContent).toContain('0.5.2');
  });

  it('stops polling on unmount and tolerates unavailable state', async () => {
    vi.mocked(ApiClient.getAppUpdate).mockRejectedValue(new Error('Unavailable'));
    const view = render(<DesktopUpdateBanner />);
    await settle();
    expect(screen.queryByRole('status')).toBeNull();
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(120_000); });
    expect(ApiClient.getAppUpdate).toHaveBeenCalledTimes(1);
  });
});
