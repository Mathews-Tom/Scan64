import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient } from '../api/client';
import { ProgressReset } from './ProgressReset';
import type { ProgressResetRead } from '../api/types';

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function confirmReset() {
  fireEvent.click(screen.getByRole('button', { name: 'Reset learning progress…' }));
  fireEvent.change(screen.getByRole('textbox', { name: 'Type RESET PROGRESS to confirm' }), { target: { value: 'RESET PROGRESS' } });
}

describe('Learning progress reset', () => {
  it('explains the boundaries and cancellation never sends a request', () => {
    const reset = vi.spyOn(ApiClient, 'resetProgress');
    render(<ProgressReset />);
    expect(screen.getByText(/Kept:/).parentElement).toHaveTextContent(/account.*saved games and analyses.*imported libraries.*settings/);
    expect(screen.getByText(/Cleared:/).parentElement).toHaveTextContent(/puzzle and lesson results.*ratings.*streaks.*mastery.*achievements.*review schedules/);
    confirmReset();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(reset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Reset learning progress…' }));
    expect(screen.getByRole('textbox')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Permanently reset learning progress' })).toBeDisabled();
  });

  it('requires the exact phrase and typing or pressing Enter does not submit', () => {
    const reset = vi.spyOn(ApiClient, 'resetProgress');
    render(<ProgressReset />);
    fireEvent.click(screen.getByRole('button', { name: 'Reset learning progress…' }));
    const input = screen.getByRole('textbox');
    const submit = screen.getByRole('button', { name: 'Permanently reset learning progress' });
    for (const value of ['', 'reset progress', 'RESET PROGRESS ', ' RESET PROGRESS']) {
      fireEvent.change(input, { target: { value } });
      expect(submit).toBeDisabled();
      fireEvent.click(submit);
      fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    }
    fireEvent.change(input, { target: { value: 'RESET PROGRESS' } });
    expect(submit).toBeEnabled();
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });
    expect(reset).not.toHaveBeenCalled();
    expect(submit).toHaveAttribute('type', 'button');
  });

  it('allows only one pending request and reloads only after confirmed success and Continue', async () => {
    const { promise, resolve } = Promise.withResolvers<ProgressResetRead>();
    const reset = vi.spyOn(ApiClient, 'resetProgress').mockReturnValue(promise);
    const reload = vi.fn();
    render(<ProgressReset reload={reload} />);
    confirmReset();
    const submit = screen.getByRole('button', { name: 'Permanently reset learning progress' });
    fireEvent.click(submit);
    fireEvent.click(screen.getByRole('button', { name: 'Resetting learning progress…' }));
    expect(reset).toHaveBeenCalledExactlyOnceWith('RESET PROGRESS');
    expect(screen.getByRole('textbox')).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Resetting learning progress…' })).toBeDisabled();
    expect(screen.queryByText(/reset successfully/)).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    await act(async () => { resolve({ player_id: 'fixture-player', reset_at: '2026-11-16T00:00:00Z', cleared: { lesson_attempts: 3 } }); });
    expect(screen.getByRole('status')).toHaveTextContent('Learning progress reset successfully');
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Continue and reload' }));
    expect(reload).toHaveBeenCalledOnce();
    expect(reset).toHaveBeenCalledOnce();
  });

  it('shows server failure without claiming completion, reloading or automatically retrying', async () => {
    const reset = vi.spyOn(ApiClient, 'resetProgress').mockRejectedValue(new Error('Not authorized for this player'));
    const reload = vi.fn();
    render(<ProgressReset reload={reload} />);
    confirmReset();
    fireEvent.click(screen.getByRole('button', { name: 'Permanently reset learning progress' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Not authorized for this player');
    expect(screen.getByRole('alert')).toHaveTextContent('No automatic retry was made');
    expect(screen.queryByText(/reset successfully/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Continue and reload' })).not.toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    expect(reset).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Permanently reset learning progress' })).toBeEnabled();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(reset).toHaveBeenCalledOnce();
  });
});
