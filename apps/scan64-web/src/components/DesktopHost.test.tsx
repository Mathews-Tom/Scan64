import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { refreshDesktopConnection, restartDesktopServer } from '../platform';
import { DesktopHost } from './DesktopHost';

vi.mock('../App', () => ({ default: () => <div>Application connected</div> }));
vi.mock('../platform', () => ({ refreshDesktopConnection: vi.fn(), restartDesktopServer: vi.fn() }));

beforeEach(() => {
  vi.useFakeTimers();
  vi.mocked(refreshDesktopConnection).mockReset().mockResolvedValue(undefined);
  vi.mocked(restartDesktopServer).mockReset().mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe('desktop failure screen', () => {
  it('shows the startup failure log path and restarts before mounting application screens', async () => {
    render(<DesktopHost initialError="Server failed. Log: /tmp/scan64/server.log" />);
    expect(screen.getByRole('alert')).toHaveTextContent('/tmp/scan64/server.log');
    expect(screen.queryByText('Application connected')).not.toBeInTheDocument();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Restart' })); });
    expect(restartDesktopServer).toHaveBeenCalledOnce();
    expect(screen.getByText('Application connected')).toBeInTheDocument();
  });

  it('replaces application screens when the server dies later', async () => {
    vi.mocked(refreshDesktopConnection).mockRejectedValueOnce('Server exited. Log: /tmp/scan64/server.log');
    render(<DesktopHost initialError={null} />);
    expect(screen.getByText('Application connected')).toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    expect(screen.getByRole('alert')).toHaveTextContent('/tmp/scan64/server.log');
    expect(screen.queryByText('Application connected')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Restart' })).toBeEnabled();
    await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
    expect(refreshDesktopConnection).toHaveBeenCalledOnce();
  });

  it('disables restart while startup is pending and keeps a failed restart visible with its log path', async () => {
    const pendingRestart = Promise.withResolvers<void>();
    vi.mocked(restartDesktopServer).mockReturnValueOnce(pendingRestart.promise);
    render(<DesktopHost initialError="Server exited. Log: /tmp/scan64/server.log" />);
    fireEvent.click(screen.getByRole('button', { name: 'Restart' }));
    expect(screen.getByRole('button', { name: 'Restarting…' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('up to 30 seconds');
    await act(async () => { pendingRestart.reject('Restart failed. Log: /tmp/scan64/new-server.log'); });
    expect(screen.getByRole('alert')).toHaveTextContent('/tmp/scan64/new-server.log');
    expect(screen.getByRole('button', { name: 'Restart' })).toBeEnabled();
  });

  it('stops polling when the desktop host is unmounted', async () => {
    const view = render(<DesktopHost initialError={null} />);
    view.unmount();
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    expect(refreshDesktopConnection).not.toHaveBeenCalled();
  });
});
