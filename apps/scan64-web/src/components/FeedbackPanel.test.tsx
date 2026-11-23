import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as platform from '../platform';
import { FeedbackPanel } from './FeedbackPanel';

vi.mock('../platform', async (original) => ({
  ...await original<typeof platform>(),
  isDesktop: vi.fn(() => true), updateStatus: vi.fn(), openFeedbackIssue: vi.fn(),
}));

beforeEach(() => {
  vi.mocked(platform.isDesktop).mockReturnValue(true);
  vi.mocked(platform.updateStatus).mockResolvedValue({ ...platform.browserUpdateStatus, installedVersion: '0.12.2' });
  vi.mocked(platform.openFeedbackIssue).mockResolvedValue();
});
afterEach(() => { cleanup(); vi.clearAllMocks(); });

function fill(summary: string, details: string) {
  fireEvent.change(screen.getByLabelText('Summary'), { target: { value: summary } });
  fireEvent.change(screen.getByLabelText('Details (optional)'), { target: { value: details } });
}

describe('FeedbackPanel', () => {
  it('opens exactly the previewed issue only after the user asks', async () => {
    render(<FeedbackPanel />);
    fireEvent.change(screen.getByLabelText('Type'), { target: { value: 'Idea' } });
    fill('Show clock in review', 'Line one\r\n\tLine two\u0007');
    const preview = screen.getByLabelText('Issue preview');
    await waitFor(() => expect(preview).toHaveTextContent('Scan64 0.12.2'));
    expect(platform.openFeedbackIssue).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: 'Open GitHub issue' }));

    await waitFor(() => expect(platform.openFeedbackIssue).toHaveBeenCalledOnce());
    const [title, body] = vi.mocked(platform.openFeedbackIssue).mock.calls[0];
    expect(title).toBe('Idea: Show clock in review');
    expect(body).toMatch(/^Line one\n {2}Line two\n\n---\nScan64 0\.12\.2 · .+ · desktop app$/);
    expect(preview.textContent).toBe(`${title}\n\n${body}`);
  });

  it('leaves out the version line when the user unticks it', async () => {
    render(<FeedbackPanel />);
    fill('Crash on import', '');
    await waitFor(() => expect(screen.getByLabelText('Issue preview')).toHaveTextContent('Scan64 0.12.2'));
    fireEvent.click(screen.getByRole('checkbox', { name: /Include the version line/ }));

    fireEvent.click(screen.getByRole('button', { name: 'Open GitHub issue' }));

    await waitFor(() => expect(platform.openFeedbackIssue).toHaveBeenCalledWith('Bug: Crash on import', ''));
  });

  it('shows the shell refusal instead of failing silently', async () => {
    vi.mocked(platform.openFeedbackIssue).mockRejectedValueOnce(new Error('Feedback is too long to open in a browser; shorten the details'));
    render(<FeedbackPanel />);
    fill('Long report', 'x');
    await waitFor(() => expect(screen.getByRole('button', { name: 'Open GitHub issue' })).toBeEnabled());

    fireEvent.click(screen.getByRole('button', { name: 'Open GitHub issue' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('shorten the details');
  });

  it('links browser users to the same prefilled GitHub form without an app command', () => {
    vi.mocked(platform.isDesktop).mockReturnValue(false);
    render(<FeedbackPanel />);
    expect(screen.getByRole('button', { name: 'Open GitHub issue' })).toBeDisabled();
    fill('Board & clock?', '');

    const link = screen.getByRole('link', { name: 'Open GitHub issue' });
    const url = new URL(link.getAttribute('href')!);
    expect(`${url.origin}${url.pathname}`).toBe('https://github.com/Mathews-Tom/Scan64/issues/new');
    expect(url.searchParams.get('title')).toBe('Bug: Board & clock?');
    expect(url.searchParams.get('body')).toMatch(/^---\nScan64 · .+ · browser$/);
    expect(link).toHaveAttribute('rel', 'noopener noreferrer');
    expect(platform.updateStatus).not.toHaveBeenCalled();
  });
});
