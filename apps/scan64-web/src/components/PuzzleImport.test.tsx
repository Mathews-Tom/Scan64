import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, choosePuzzleFile, isDesktop } from '../platform';
import { PuzzleImportFoot, PuzzleImportProvider, PuzzleLibraryControls } from './PuzzleImport';
import type { PuzzleImportState } from './PuzzleImport';

vi.mock('../platform', () => ({ apiBase: () => '/v1', apiFetch: vi.fn(), isDesktop: vi.fn(), choosePuzzleFile: vi.fn() }));
const idle: PuzzleImportState = { status: 'idle', phase: null, done: 0, total: null, unit: 'puzzles', bytes_per_second: null, error: null, summary: null };
let state: PuzzleImportState;
const startPuzzles = vi.fn();
function Harness() {
  const [page, setPage] = useState('puzzles');
  return <PuzzleImportProvider onStartPuzzles={startPuzzles}>
    <button onClick={() => setPage(page === 'puzzles' ? 'settings' : 'puzzles')}>Navigate</button>
    <PuzzleImportFoot />
    <section key={page}><h1>{page}</h1><PuzzleLibraryControls /></section>
  </PuzzleImportProvider>;
}
async function settle() { await act(async () => { await Promise.resolve(); }); }
beforeEach(() => {
  vi.useFakeTimers();
  state = { ...idle };
  vi.mocked(isDesktop).mockReturnValue(true);
  vi.mocked(choosePuzzleFile).mockResolvedValue(null);
  vi.mocked(apiFetch).mockImplementation(async (_url, options) => {
    if (options?.method === 'POST') state = { ...idle, status: 'running', phase: 'download', done: 2_000_000, total: 10_000_000, unit: 'bytes', bytes_per_second: 1_000_000 };
    if (options?.method === 'DELETE') state = { ...state, status: 'cancelling' };
    return new Response(JSON.stringify(state), { status: 200 });
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

describe('Puzzle import', () => {
  it('requires a click, describes the source and filters, and starts through the platform API', async () => {
    render(<Harness />); await settle();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(screen.getByText(/about 270 MB/)).toHaveTextContent('50,000 puzzles rated 600–1800');
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    expect(apiFetch).toHaveBeenCalledWith('/v1/app/puzzle-import', expect.objectContaining({ method: 'POST', body: JSON.stringify({ source: 'download' }) }));
    expect(screen.getByRole('progressbar', { name: 'Download' })).toHaveAttribute('value', '2000000');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', 'Download · 2.0 MB of 10.0 MB · 1.0 MB/s · 8 seconds left');
  });
  it('survives navigation, polls every 500ms, and stops after completion', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Navigate' }));
    expect(screen.getByRole('heading', { name: 'settings' })).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    const before = vi.mocked(apiFetch).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(499); });
    expect(apiFetch).toHaveBeenCalledTimes(before);
    state = { ...state, status: 'succeeded', summary: { accepted: 50, inserted: 40, existing: 10, read: 100 } };
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(screen.getByRole('button', { name: 'Start puzzles' })).toBeInTheDocument();
    expect(document.querySelector('.puzzle-import-foot')).toBeNull();
    await settle();
    const completed = vi.mocked(apiFetch).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(apiFetch).toHaveBeenCalledTimes(completed);
    fireEvent.click(screen.getByRole('button', { name: 'Start puzzles' }));
    expect(startPuzzles).toHaveBeenCalledOnce();
  });
  it('cancels downloads and waits for terminal cancellation', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Cancel download' })); await settle();
    expect(apiFetch).toHaveBeenCalledWith('/v1/app/puzzle-import', { method: 'DELETE' });
    expect(screen.getByRole('button', { name: 'Cancel download' })).toHaveAttribute('aria-disabled', 'true');
    expect(screen.getByRole('button', { name: 'Cancel download' })).toBeEnabled();
    state = { ...state, status: 'cancelled' };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.getByText('Import cancelled. Your puzzle library is unchanged.')).toBeInTheDocument();
  });
  it('uses the native file selection and does not start when dismissed', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Choose a file…' })); await settle();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    vi.mocked(choosePuzzleFile).mockResolvedValue('/tmp/puzzles.csv.zst');
    fireEvent.click(screen.getByRole('button', { name: 'Choose a file…' })); await settle();
    expect(apiFetch).toHaveBeenCalledWith('/v1/app/puzzle-import', expect.objectContaining({ method: 'POST', body: JSON.stringify({ source: 'file', path: '/tmp/puzzles.csv.zst' }) }));
  });
  it.each(['read', 'import', 'save'] as const)('restores the %s phase and offers cancel import', async phase => {
    state = { ...idle, status: 'running', phase, done: 10, total: 50 };
    render(<Harness />); await settle();
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', phase === 'save' ? 'Saving puzzle catalog…' : expect.stringContaining('10 puzzles of 50 puzzles'));
    expect(screen.getByRole('button', { name: 'Cancel import' })).toBeEnabled();
  });
  it('shows failures and allows another attempt', async () => {
    state = { ...idle, status: 'failed', error: 'File is not readable.' };
    render(<Harness />); await settle();
    expect(screen.getByRole('alert')).toHaveTextContent('File is not readable.');
    expect(screen.getByRole('button', { name: 'Download puzzles' })).toBeEnabled();
  });
  it('shows streamed bytes without inventing an unknown total or ETA', async () => {
    state = { ...idle, status: 'running', phase: 'download', unit: 'bytes', done: 2_000_000, bytes_per_second: 1_000_000 };
    render(<Harness />); await settle();
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', 'Download · 2.0 MB · 1.0 MB/s');
  });
  it('shows API failures without claiming the import succeeded', async () => {
    render(<Harness />); await settle();
    vi.mocked(apiFetch).mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'Another import is running.' }), { status: 409 }));
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    expect(screen.getByRole('alert')).toHaveTextContent('Another import is running.');
    expect(screen.queryByRole('button', { name: 'Start puzzles' })).toBeNull();
  });
  it('keeps keyboard focus on Cancel while cancelling and on the terminal status afterward', async () => {
    render(<Harness />); await settle();
    const download = screen.getByRole('button', { name: 'Download puzzles' });
    download.focus();
    fireEvent.click(download); await settle();
    const cancel = screen.getByRole('button', { name: 'Cancel download' });
    expect(document.activeElement).toBe(cancel);
    fireEvent.click(cancel); await settle();
    expect(document.activeElement).toBe(cancel);
    expect(cancel).toHaveAttribute('aria-disabled', 'true');
    const deletes = vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'DELETE').length;
    fireEvent.click(cancel); await settle();
    expect(vi.mocked(apiFetch).mock.calls.filter(([, options]) => options?.method === 'DELETE')).toHaveLength(deletes);
    state = { ...state, status: 'cancelled' };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(document.activeElement).toBe(screen.getByText('Import cancelled. Your puzzle library is unchanged.'));
  });
  it('focuses Start puzzles after a successful import', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    state = { ...state, status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Start puzzles' }));
  });
  it('focuses failures and keeps Retry actions available', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    state = { ...state, status: 'failed', error: 'The download failed.' };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(document.activeElement).toBe(screen.getByRole('alert'));
    expect(screen.getByRole('button', { name: 'Download puzzles' })).toBeEnabled();
  });
  it('preserves string picker errors and focuses the message', async () => {
    render(<Harness />); await settle();
    vi.mocked(choosePuzzleFile).mockRejectedValueOnce('Puzzle file is not readable');
    fireEvent.click(screen.getByRole('button', { name: 'Choose a file…' })); await settle();
    expect(screen.getByRole('alert')).toHaveTextContent('Puzzle file is not readable');
    expect(document.activeElement).toBe(screen.getByRole('alert'));
  });
  it('does not offer Start puzzles when no rows matched the filters', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    state = { ...state, status: 'succeeded', summary: { accepted: 0, inserted: 0, existing: 0, read: 3 } };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.queryByRole('button', { name: 'Start puzzles' })).toBeNull();
    expect(document.activeElement).toBe(screen.getByText(/No puzzles matched the filters/));
  });
  it('keeps post-commit cleanup warnings distinct from import failure', async () => {
    state = { ...idle, status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 }, error: 'Puzzles were imported, but cleanup did not finish normally.' };
    render(<Harness />); await settle();
    expect(screen.getByText(/cleanup did not finish normally/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start puzzles' })).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
  });
  it.each(['controls', 'body', 'elsewhere'] as const)('respects %s focus when an import finishes', async location => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const cancel = screen.getByRole('button', { name: 'Cancel download' });
    const elsewhere = screen.getByRole('button', { name: 'Navigate' });
    if (location === 'body') cancel.blur();
    else if (location === 'elsewhere') elsewhere.focus();
    else cancel.focus();
    state = { ...state, status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } };
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(document.activeElement).toBe(location === 'elsewhere' ? elsewhere : screen.getByRole('button', { name: 'Start puzzles' }));
  });
  it.each(['controls', 'body', 'elsewhere'] as const)('respects %s focus when polling fails', async location => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const cancel = screen.getByRole('button', { name: 'Cancel download' });
    const elsewhere = screen.getByRole('button', { name: 'Navigate' });
    if (location === 'body') cancel.blur();
    else if (location === 'elsewhere') elsewhere.focus();
    else cancel.focus();
    vi.mocked(apiFetch).mockRejectedValueOnce(new Error('Import status is unavailable.'));
    await act(async () => { await vi.advanceTimersByTimeAsync(500); });
    expect(screen.getByRole('alert')).toHaveTextContent('Import status is unavailable.');
    expect(document.activeElement).toBe(location === 'elsewhere' ? elsewhere : screen.getByRole('alert'));
  });
  it('keeps CLI instructions and makes no desktop requests in browser installs', async () => {
    vi.mocked(isDesktop).mockReturnValue(false);
    render(<Harness />); await settle();
    expect(screen.getByText(/uv run scan64-cli/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Download puzzles' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Choose a file…' })).toBeNull();
    expect(apiFetch).not.toHaveBeenCalled();
    expect(choosePuzzleFile).not.toHaveBeenCalled();
  });
});
