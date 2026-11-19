import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { apiFetch, choosePuzzleFile, isDesktop } from '../platform';
import { PuzzleImportFoot, PuzzleImportProvider, PuzzleLibraryControls } from './PuzzleImport';
import type { PuzzleImportState } from './PuzzleImport';

vi.mock('../platform', () => ({ apiBase: () => '/v1', apiFetch: vi.fn(), isDesktop: vi.fn(), choosePuzzleFile: vi.fn() }));
const idle: PuzzleImportState = { revision: 0, status: 'idle', phase: null, done: 0, total: null, unit: 'puzzles', bytes_per_second: null, error: null, summary: null };
let state: PuzzleImportState;
interface ProgressStream {
  body: ReadableStream<Uint8Array>;
  cancel: Mock;
  response: Response;
  push: (next: PuzzleImportState) => void;
  fail: (error: Error) => void;
}
let streams: ProgressStream[];
function progressStream(initial?: PuzzleImportState) {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(value) {
      controller = value;
      if (initial) controller.enqueue(new TextEncoder().encode(`${JSON.stringify(initial)}\n`));
    },
    cancel,
  });
  return {
    body,
    cancel,
    response: new Response(body, { headers: { 'Content-Type': 'application/x-ndjson' } }),
    push: (next: PuzzleImportState) => controller.enqueue(new TextEncoder().encode(`${JSON.stringify(next)}\n`)),
    fail: (error: Error) => controller.error(error),
  };
}
async function pushState(next: Partial<PuzzleImportState>) {
  state = { ...state, ...next, revision: state.revision + 1 };
  await act(async () => { streams.at(-1)!.push(state); });
}
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
  streams = [];
  vi.mocked(isDesktop).mockReturnValue(true);
  vi.mocked(choosePuzzleFile).mockResolvedValue(null);
  vi.mocked(apiFetch).mockImplementation(async (url, options) => {
    if (url.endsWith('/events')) {
      const stream = progressStream(state);
      streams.push(stream);
      return stream.response;
    }
    if (options?.method === 'POST') state = { ...idle, revision: state.revision + 1, status: 'running', phase: 'download', done: 2_000_000, total: 10_000_000, unit: 'bytes', bytes_per_second: 1_000_000 };
    if (options?.method === 'DELETE') state = { ...state, revision: state.revision + 1, status: 'cancelling' };
    return new Response(JSON.stringify(state), { status: 200 });
  });
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

describe('Puzzle import', () => {
  it('requires a click, describes the source and filters, and starts through the platform API', async () => {
    render(<Harness />); await settle();
    expect(apiFetch).toHaveBeenCalledTimes(1);
    expect(apiFetch).toHaveBeenCalledWith('/v1/app/puzzle-import/events', expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(streams[0].cancel).toHaveBeenCalledOnce();
    expect(screen.getByText(/about 270 MB/)).toHaveTextContent('50,000 puzzles rated 600–1800');
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    expect(apiFetch).toHaveBeenCalledWith('/v1/app/puzzle-import', expect.objectContaining({ method: 'POST', body: JSON.stringify({ source: 'download' }) }));
    expect(screen.getByRole('progressbar', { name: 'Download' })).toHaveAttribute('value', '2000000');
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', 'Download · 2.0 MB of 10.0 MB · 1.0 MB/s · 8 seconds left');
  });
  it('survives navigation, displays pushed progress and never polls after completion', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Navigate' }));
    expect(screen.getByRole('heading', { name: 'settings' })).toBeInTheDocument();
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /^Puzzle import:/ })).toBeInTheDocument();
    const before = vi.mocked(apiFetch).mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiFetch).toHaveBeenCalledTimes(before);
    await pushState({ phase: 'import', unit: 'puzzles', done: 30, total: 50, bytes_per_second: null });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', 'Import · 30 puzzles of 50 puzzles');
    expect(screen.getByRole('link', { name: /^Puzzle import:/ })).toHaveTextContent('30 puzzles of 50 puzzles');
    await pushState({ status: 'succeeded', summary: { accepted: 50, inserted: 40, existing: 10, read: 100 } });
    expect(screen.getByRole('button', { name: 'Start puzzles' })).toBeInTheDocument();
    expect(screen.getByText('Puzzle library ready. 40 added; 10 already in your library.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^Puzzle import:/ })).toBeNull();
    expect(streams.at(-1)!.cancel).toHaveBeenCalledOnce();
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiFetch).toHaveBeenCalledTimes(before);
    expect(vi.getTimerCount()).toBe(0);
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
    await pushState({ status: 'cancelled' });
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
    await pushState({ status: 'cancelled' });
    expect(document.activeElement).toBe(screen.getByText('Import cancelled. Your puzzle library is unchanged.'));
  });
  it('focuses Start puzzles after a successful import', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    await pushState({ status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Start puzzles' }));
  });
  it('focuses failures and keeps Retry actions available', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    await pushState({ status: 'failed', error: 'The download failed.' });
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
    await pushState({ status: 'succeeded', summary: { accepted: 0, inserted: 0, existing: 0, read: 3 } });
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
    await pushState({ status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } });
    expect(document.activeElement).toBe(location === 'elsewhere' ? elsewhere : screen.getByRole('button', { name: 'Start puzzles' }));
  });
  it.each(['controls', 'body', 'elsewhere'] as const)('respects %s focus when the progress stream fails', async location => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const cancel = screen.getByRole('button', { name: 'Cancel download' });
    const elsewhere = screen.getByRole('button', { name: 'Navigate' });
    if (location === 'body') cancel.blur();
    else if (location === 'elsewhere') elsewhere.focus();
    else cancel.focus();
    await act(async () => { streams.at(-1)!.fail(new Error('Import status is unavailable.')); });
    expect(screen.getByRole('alert')).toHaveTextContent('Import status is unavailable.');
    expect(document.activeElement).toBe(location === 'elsewhere' ? elsewhere : screen.getByRole('alert'));
  });
  it('ignores a delayed initial response after a newer action and pushed revision', async () => {
    const initialResponse = Promise.withResolvers<Response>();
    vi.mocked(apiFetch).mockImplementationOnce(() => initialResponse.promise);
    render(<Harness />); await settle();
    const initialSignal = vi.mocked(apiFetch).mock.calls[0][1]!.signal!;
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    await pushState({ phase: 'import', unit: 'puzzles', done: 25, total: 50, bytes_per_second: null });
    const stale = progressStream(idle);
    await act(async () => { initialResponse.resolve(stale.response); });
    expect(initialSignal.aborted).toBe(true);
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', 'Import · 25 puzzles of 50 puzzles');
    expect(screen.queryByRole('button', { name: 'Download puzzles' })).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
    await pushState({ status: 'succeeded', summary: { accepted: 25, inserted: 20, existing: 5, read: 50 } });
    expect(screen.getByText('Puzzle library ready. 20 added; 5 already in your library.')).toBeInTheDocument();
  });
  it('keeps a terminal event when a running POST response arrives afterward', async () => {
    const live = progressStream();
    vi.mocked(apiFetch).mockResolvedValueOnce(live.response);
    render(<Harness />); await settle();
    const actionResponse = Promise.withResolvers<Response>();
    vi.mocked(apiFetch).mockImplementationOnce(() => actionResponse.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const running: PuzzleImportState = { ...idle, revision: 1, status: 'running', phase: 'import', done: 1 };
    const completed: PuzzleImportState = { ...running, revision: 2, status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } };
    await act(async () => { live.push(running); });
    await act(async () => { live.push(completed); });
    expect(screen.getByRole('button', { name: 'Start puzzles' })).toBeInTheDocument();
    await act(async () => { actionResponse.resolve(new Response(JSON.stringify(running))); });
    expect(screen.getByText('Puzzle library ready. 1 added; 0 already in your library.')).toBeInTheDocument();
    expect(screen.queryByRole('progressbar')).toBeNull();
    expect(screen.queryByRole('link', { name: /^Puzzle import:/ })).toBeNull();
    expect(live.cancel).toHaveBeenCalledOnce();
    expect(apiFetch).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiFetch).toHaveBeenCalledTimes(2);
  });
  it('does not regress an action revision when older or duplicate events arrive', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const older = { ...state };
    fireEvent.click(screen.getByRole('button', { name: 'Cancel download' })); await settle();
    const revision = state.revision;
    await act(async () => {
      streams.at(-1)!.push(older);
      streams.at(-1)!.push({ ...older, revision });
    });
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuetext', expect.stringMatching(/^Cancelling/));
    expect(screen.getByRole('button', { name: 'Cancel download' })).toHaveAttribute('aria-disabled', 'true');
    await pushState({ status: 'cancelled' });
    expect(screen.getByText('Import cancelled. Your puzzle library is unchanged.')).toBeInTheDocument();
  });
  it('shows a failed stream without scheduling polls or a reconnect', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const active = streams.at(-1)!;
    const requests = vi.mocked(apiFetch).mock.calls.length;
    await act(async () => { active.fail(new Error('Progress connection lost.')); });
    expect(screen.getByRole('alert')).toHaveTextContent('Progress connection lost.');
    expect(screen.queryByRole('button', { name: 'Start puzzles' })).toBeNull();
    expect(active.body.locked).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiFetch).toHaveBeenCalledTimes(requests);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('opens one fresh stream for a new start after a terminal import', async () => {
    render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const first = streams.at(-1)!;
    await pushState({ status: 'succeeded', summary: { accepted: 1, inserted: 1, existing: 0, read: 1 } });
    expect(first.cancel).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    expect(streams).toHaveLength(3);
    expect(streams.at(-1)).not.toBe(first);
    expect(screen.getByRole('progressbar')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Start puzzles' })).toBeNull();
    await pushState({ status: 'succeeded', summary: { accepted: 3, inserted: 2, existing: 1, read: 4 } });
    expect(screen.getByText('Puzzle library ready. 2 added; 1 already in your library.')).toBeInTheDocument();
    expect(streams.at(-1)!.cancel).toHaveBeenCalledOnce();
    expect(vi.mocked(apiFetch).mock.calls.filter(([url]) => url.endsWith('/events'))).toHaveLength(3);
  });
  it('aborts and releases the active stream on provider disposal', async () => {
    const view = render(<Harness />); await settle();
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    const active = streams.at(-1)!;
    const call = vi.mocked(apiFetch).mock.calls.filter(([url]) => url.endsWith('/events')).at(-1)!;
    const signal = call[1]!.signal!;
    expect(signal.aborted).toBe(false);
    expect(active.body.locked).toBe(true);
    const requests = vi.mocked(apiFetch).mock.calls.length;
    view.unmount(); await settle();
    expect(signal.aborted).toBe(true);
    expect(active.cancel).toHaveBeenCalledExactlyOnceWith(signal.reason);
    expect(active.body.locked).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(apiFetch).toHaveBeenCalledTimes(requests);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('aborts a pending initial request and does not subscribe after a pending action is disposed', async () => {
    const initialResponse = Promise.withResolvers<Response>();
    vi.mocked(apiFetch).mockImplementationOnce(() => initialResponse.promise);
    const view = render(<Harness />); await settle();
    const signal = vi.mocked(apiFetch).mock.calls[0][1]!.signal!;
    const actionResponse = Promise.withResolvers<Response>();
    vi.mocked(apiFetch).mockImplementationOnce(() => actionResponse.promise);
    fireEvent.click(screen.getByRole('button', { name: 'Download puzzles' })); await settle();
    view.unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => {
      initialResponse.resolve(progressStream(idle).response);
      actionResponse.resolve(new Response(JSON.stringify({ ...idle, revision: 1, status: 'running' })));
    });
    expect(apiFetch).toHaveBeenCalledTimes(2);
    expect(vi.getTimerCount()).toBe(0);
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
