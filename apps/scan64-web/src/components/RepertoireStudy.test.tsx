import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiRequestError } from '../api/client';
import { RepertoireApi, type RepertoireRead } from '../api/repertoire';
import { commitAnalysisMoves, createAnalysisTree } from './analysisTree';
import { RepertoireStudy } from './RepertoireStudy';

vi.mock('chessground', () => ({
  Chessground: () => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }),
}));
vi.mock('../api/repertoire', () => ({ RepertoireApi: { list: vi.fn(), importPgn: vi.fn(), review: vi.fn() } }));

function fixture(): RepertoireRead {
  let tree = commitAnalysisMoves(createAnalysisTree(), 0, ['e2e4', 'e7e5', 'g1f3']).tree;
  tree = commitAnalysisMoves(tree, 0, ['d2d4', 'd7d5']).tree;
  tree = commitAnalysisMoves(tree, 4, ['g8f6']).tree;
  tree = commitAnalysisMoves(tree, 1, ['c7c5', 'g1f3']).tree;
  const decisions = tree.nodes.filter(node => node.children.length).map(node => ({ node_id: node.id, reviewed: false, due: true, next_review_at: null }));
  return { id: 'saved-repertoire', title: 'Nested repertoire', tree, decisions, total_decisions: decisions.length, reviewed_decisions: 0, due_decisions: decisions.length };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(RepertoireApi.list).mockResolvedValue([]);
});

async function importTree() {
  const saved = fixture();
  vi.mocked(RepertoireApi.importPgn).mockResolvedValue([saved]);
  render(<RepertoireStudy />);
  await waitFor(() => expect(screen.getByLabelText('Repertoire PGN')).toBeEnabled());
  fireEvent.change(screen.getByLabelText('Repertoire PGN'), { target: { value: '1. e4 (1. d4 d5 (1... Nf6)) e5 *' } });
  fireEvent.click(screen.getByRole('button', { name: 'Import repertoire' }));
  await screen.findByRole('region', { name: 'Saved repertoire playback' });
  return saved;
}

function move(uci: string) {
  fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: uci } });
  fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
}

describe('owned repertoire study', () => {
  it('imports, navigates main and nested alternatives without changing the retained tree', async () => {
    const saved = await importTree();
    const before = JSON.stringify(saved.tree);
    expect(RepertoireApi.importPgn).toHaveBeenCalledWith('1. e4 (1. d4 d5 (1... Nf6)) e5 *');
    expect(screen.getByText(/Coverage: 0 \/ 5/)).toBeInTheDocument();
    const variations = within(screen.getByRole('list', { name: 'Repertoire variations' }));
    fireEvent.click(variations.getByRole('button', { name: '1... e5' }));
    expect(screen.getByText('e5: Black pawn')).toBeInTheDocument();
    fireEvent.click(variations.getByRole('button', { name: 'Variation: 1... Nf6' }));
    expect(screen.getByText('d4: White pawn')).toBeInTheDocument();
    expect(screen.getByText('f6: Black knight')).toBeInTheDocument();
    expect(screen.queryByText('e5: Black pawn')).not.toBeInTheDocument();
    expect(JSON.stringify(saved.tree)).toBe(before);
    expect(RepertoireApi.review).not.toHaveBeenCalled();
  });

  it('records a verified keyboard drill, refreshes due/coverage and reloads the server state', async () => {
    const saved = await importTree();
    const updated: RepertoireRead = { ...saved, reviewed_decisions: 1, due_decisions: 4,
      decisions: saved.decisions.map(item => item.node_id === 0 ? { ...item, reviewed: true, due: false, next_review_at: '2026-12-01T12:00:00Z' } : item) };
    vi.mocked(RepertoireApi.review).mockResolvedValue({ attempt_id: 'attempt', success: true, rating: 3, next_review_at: '2026-12-01T12:00:00Z', repertoire: updated });
    fireEvent.click(screen.getByRole('button', { name: 'Practise next due' }));
    expect(screen.queryByRole('list', { name: 'Repertoire variations' })).not.toBeInTheDocument();
    move('e4');
    await screen.findByText(/Saved continuation — reviewed/);
    expect(RepertoireApi.review).toHaveBeenCalledWith(saved.id, expect.objectContaining({ node_id: 0, submitted_move: 'e2e4', attempt_id: expect.any(String) }));
    expect(screen.getByText(/Coverage: 1 \/ 5/)).toBeInTheDocument();
    expect(screen.getByText(/Due: 4;/)).toBeInTheDocument();
    vi.mocked(RepertoireApi.list).mockResolvedValue([updated]);
    // Re-mount is a fresh screen load: state comes from the API, not browser storage.
    const second = render(<RepertoireStudy />);
    const scope = within(second.container);
    fireEvent.click(await scope.findByRole('button', { name: 'Nested repertoire' }));
    expect(scope.getByText(/Coverage: 1 \/ 5/)).toBeInTheDocument();
    expect(scope.getByText(/Reviewed · Next due/)).toBeInTheDocument();
  });

  it('retains the same identity and payload after an uncertain response', async () => {
    const saved = await importTree();
    vi.mocked(RepertoireApi.review).mockRejectedValueOnce(new Error('Connection interrupted'));
    vi.mocked(RepertoireApi.review).mockResolvedValueOnce({ attempt_id: 'attempt', success: false, rating: 1, next_review_at: '2026-12-01T12:00:00Z', repertoire: saved });
    fireEvent.click(screen.getByRole('button', { name: 'Practise this position' }));
    move('a3');
    fireEvent.click(await screen.findByRole('button', { name: 'Retry same submission' }));
    await screen.findByText(/not a saved continuation — Again/);
    expect(RepertoireApi.review).toHaveBeenCalledTimes(2);
    expect(vi.mocked(RepertoireApi.review).mock.calls[0]).toEqual(vi.mocked(RepertoireApi.review).mock.calls[1]);
  });

  it('unlocks practice and removes a repertoire that the server no longer owns', async () => {
    const saved = await importTree();
    const other = { ...saved, id: 'other', title: 'Other repertoire' };
    vi.mocked(RepertoireApi.list).mockResolvedValue([other]);
    vi.mocked(RepertoireApi.review).mockRejectedValueOnce(new ApiRequestError('Repertoire not found', 404));
    fireEvent.click(screen.getByRole('button', { name: 'Practise this position' }));
    move('e4');
    expect(await screen.findByRole('alert')).toHaveTextContent('Repertoire not found');
    expect(await screen.findByRole('button', { name: 'Other repertoire' })).toBeEnabled();
    expect(screen.queryByRole('button', { name: 'Retry same submission' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: saved.title })).not.toBeInTheDocument();
    expect(screen.getByLabelText('Repertoire PGN')).toBeEnabled();
  });

  it.each(['deadline', 'visible'] as const)('refreshes stale due state on %s without a reload', async trigger => {
    const saved = fixture();
    const dueAt = Date.now() + 5_000;
    const waiting: RepertoireRead = {
      ...saved,
      reviewed_decisions: saved.total_decisions,
      due_decisions: 0,
      decisions: saved.decisions.map(item => ({
        ...item, reviewed: true, due: false, next_review_at: new Date(dueAt).toISOString(),
      })),
    };
    vi.mocked(RepertoireApi.list).mockResolvedValue([waiting]);
    vi.useFakeTimers();
    const visibility = vi.spyOn(document, 'visibilityState', 'get');
    try {
      visibility.mockReturnValue('visible');
      render(<RepertoireStudy />);
      await act(async () => {});
      fireEvent.click(screen.getByRole('button', { name: saved.title }));
      expect(screen.getByRole('button', { name: 'Practise next due' })).toBeDisabled();
      vi.mocked(RepertoireApi.list).mockResolvedValue([{
        ...waiting,
        due_decisions: 1,
        decisions: waiting.decisions.map(item => ({ ...item, due: item.node_id === 0 })),
      }]);
      if (trigger === 'visible') visibility.mockReturnValue('hidden');
      await act(() => vi.advanceTimersByTimeAsync(5_000));
      if (trigger === 'visible') {
        expect(screen.getByRole('button', { name: 'Practise next due' })).toBeDisabled();
        visibility.mockReturnValue('visible');
        await act(async () => { document.dispatchEvent(new Event('visibilitychange')); });
      }
      expect(screen.getByRole('button', { name: 'Practise next due' })).toBeEnabled();
    } finally {
      visibility.mockRestore();
      vi.useRealTimers();
    }
  });

  it('does not submit illegal keyboard input and displays atomic import errors', async () => {
    await importTree();
    fireEvent.click(screen.getByRole('button', { name: 'Practise this position' }));
    move('e2e8');
    expect(await screen.findByRole('alert')).toHaveTextContent('Enter a legal move');
    expect(RepertoireApi.review).not.toHaveBeenCalled();
    vi.mocked(RepertoireApi.importPgn).mockRejectedValueOnce(new Error('Invalid PGN move: e8'));
    fireEvent.change(screen.getByLabelText('Repertoire PGN'), { target: { value: '1. e8 *' } });
    fireEvent.click(screen.getByRole('button', { name: 'Import repertoire' }));
    await screen.findByText('Invalid PGN move: e8');
    expect(screen.getByRole('list', { name: 'Saved repertoires' }).children).toHaveLength(1);
  });
});
