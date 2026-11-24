import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LessonPacksApi, type LessonRead, type PackRead } from '../api/lessonPacks';
import { LessonPacksPanel } from './LessonPacksPanel';
import { PackLessonPlayer } from './PackLessonPlayer';

vi.mock('chessground', () => ({ Chessground: () => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }) }));
vi.mock('../api/lessonPacks', () => ({ MAX_PACK_BYTES: 512 * 1024, LessonPacksApi: {
  list: vi.fn(), install: vi.fn(), retry: vi.fn(), uninstall: vi.fn(), lesson: vi.fn(), check: vi.fn(),
} }));
const lesson: LessonRead = { id: 'mate', title: 'Finish', pack_id: 'sample', pack_title: 'Sample pack', author: '<b>Author</b>', fen: '7k/5Q2/6K1/8/8/8/8/8 w - - 0 1', objective: 'Find mate', themes: ['mate'] };
const pack: PackRead = { id: 'sample', title: 'Sample pack', author: '<b>Author</b>', licence: 'CC0-1.0', version: '1.0', description: null, source: null, sha256: 'a'.repeat(64), installed_at: '2026-11-24T12:00:00Z', state: 'verified', reason: null, lessons: [
  { id: 'mate', title: 'Finish', state: 'verified', failing_ply: null, reason: null },
  { id: 'bad', title: 'Unsound', state: 'rejected', failing_ply: 1, reason: 'Move is not sound' },
] };
beforeEach(() => { vi.resetAllMocks(); vi.mocked(LessonPacksApi.list).mockResolvedValue([pack]); vi.mocked(LessonPacksApi.lesson).mockResolvedValue(lesson); });
afterEach(() => vi.useRealTimers());

describe('community lesson packs', () => {
  it('shows provenance and rejection reasons as text and opens only verified lessons', async () => {
    render(<LessonPacksPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Open Finish' }));
    expect(screen.getByText(/Unsound — rejected \(ply 1\): Move is not sound/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Open Unsound' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Retry/ })).not.toBeInTheDocument();
    expect(screen.getByText(/Author: <b>Author<\/b> · Licence: CC0-1.0 · Version: 1.0/)).toBeInTheDocument();
    await screen.findByText(/Find mate/);
    expect(LessonPacksApi.lesson).toHaveBeenCalledWith('sample', 'mate');
  });
  it('rejects oversized files before uploading and sends the original accepted file', async () => {
    render(<LessonPacksPanel />);
    const input = screen.getByLabelText('Lesson pack JSON file');
    fireEvent.change(input, { target: { files: [new File([new Uint8Array(512 * 1024 + 1)], 'large.json')] } });
    expect(await screen.findByRole('alert')).toHaveTextContent('at most 512 KiB');
    expect(LessonPacksApi.install).not.toHaveBeenCalled();
    const file = new File(['{"format":"scan64-lesson-pack"}'], 'pack.json');
    vi.mocked(LessonPacksApi.install).mockResolvedValue(pack);
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(LessonPacksApi.install).toHaveBeenCalledWith(file));
  });
  it('requires explicit uninstall confirmation', async () => {
    render(<LessonPacksPanel />);
    fireEvent.click(await screen.findByRole('button', { name: 'Uninstall Sample pack' }));
    expect(LessonPacksApi.uninstall).not.toHaveBeenCalled();
    vi.mocked(LessonPacksApi.list).mockResolvedValue([]);
    fireEvent.click(screen.getByRole('button', { name: 'Confirm uninstall Sample pack' }));
    await waitFor(() => expect(LessonPacksApi.uninstall).toHaveBeenCalledWith('sample'));
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Open Finish' })).not.toBeInTheDocument());
  });
  it('polls pending verification and offers retry for unavailable verification', async () => {
    vi.useFakeTimers();
    vi.mocked(LessonPacksApi.list).mockResolvedValueOnce([{ ...pack, state: 'pending' }]).mockResolvedValue([{ ...pack, state: 'unavailable', reason: 'No engine' }]);
    render(<LessonPacksPanel />);
    await act(async () => { await Promise.resolve(); });
    expect(screen.queryByRole('button', { name: 'Open Finish' })).not.toBeInTheDocument();
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); });
    expect(screen.getByText('Verification unavailable: No engine')).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Sample pack lessons' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry Sample pack' }));
    await act(async () => { await Promise.resolve(); });
    expect(LessonPacksApi.retry).toHaveBeenCalledWith('sample');
  });
});

describe('community lesson player', () => {
  it('requests hints without moving and checks keyboard moves on the server before completion', async () => {
    vi.mocked(LessonPacksApi.check).mockResolvedValueOnce({ accepted: false, fen: lesson.fen, ply: 0, reply: null, complete: false, hints: ['Protect the queen'], explanation: null })
      .mockResolvedValueOnce({ accepted: false, fen: lesson.fen, ply: 0, reply: null, complete: false, hints: ['Protect the queen'], explanation: null })
      .mockResolvedValueOnce({ accepted: true, fen: '7k/6Q1/6K1/8/8/8/8/8 b - - 1 1', ply: 1, reply: null, complete: true, hints: [], explanation: 'Mate explanation' });
    render(<PackLessonPlayer lesson={lesson} onClose={vi.fn()} />);
    expect(screen.queryByText('Mate explanation')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Show hint' }));
    await screen.findByText('Protect the queen');
    expect(LessonPacksApi.check).toHaveBeenLastCalledWith('sample', 'mate', { ply: 0, hint: true });
    function move(value: string) {
      fireEvent.change(screen.getByRole('textbox', { name: /move/i }), { target: { value } });
      fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    }
    move('f7e6');
    await screen.findByText('That is not the pack continuation. Try again.');
    move('f7g7');
    await screen.findByText('Mate explanation');
    expect(LessonPacksApi.check).toHaveBeenLastCalledWith('sample', 'mate', { ply: 0, move: 'f7g7' });
    expect(screen.getByRole('button', { name: 'Show hint' })).toBeDisabled();
  });
});
