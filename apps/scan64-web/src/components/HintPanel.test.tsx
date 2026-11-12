import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import { ApiClient } from '../api/client';
import { HintPanel } from './HintPanel';
import { linePosition } from './linePosition';
import type { PositionInsight } from '../api/types';

const fen = new Chess().fen();
const insight: PositionInsight = { step: 3, fen, visualizations: [], candidates: [{ number: 1, move: 'e2e4', san: 'e4', line: ['e2e4', 'e7e5'], reasons: [], fallback: true, visualizations: [] }] };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
describe('HintPanel', () => {
  it('requests the active session and previews without submitting a game move', async () => {
    const request = vi.spyOn(ApiClient, 'getPositionInsight')
      .mockResolvedValueOnce({ step: 1, text: 'What do you notice?', visualizations: [{ command: 'highlight_square', description: 'Inspect', square: 'e2' }] })
      .mockResolvedValueOnce({ step: 2, text: 'Consider your pawn.', motif: 'engine preference', piece: 'pawn', square: 'e2', targets: [], visualizations: [{ command: 'highlight_piece', description: 'Pawn', square: 'e2' }] })
      .mockResolvedValueOnce(insight);
    const submit = vi.spyOn(ApiClient, 'makePlaySessionMove');
    const onPreview = vi.fn();
    render(<HintPanel playerId="p" sessionId="s" fen={fen} onPreview={onPreview} />);
    fireEvent.click(screen.getByRole('button', { name: 'Hint' }));
    await screen.findByText('What do you notice?');
    expect(request).toHaveBeenLastCalledWith({ player_id: 'p', play_session_id: 's', step: 1 });
    expect(onPreview).toHaveBeenLastCalledWith(expect.objectContaining({ visualizations: [expect.objectContaining({ command: 'highlight_square', square: 'e2' })] }));
    expect(screen.queryByText('1. e4')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hint' }));
    await screen.findByText('Consider your pawn.');
    expect(request).toHaveBeenLastCalledWith({ player_id: 'p', play_session_id: 's', step: 2 });
    expect(screen.queryByText('1. e4')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hint' }));
    await screen.findByText('engine preference');
    expect(request).toHaveBeenLastCalledWith({ player_id: 'p', play_session_id: 's', step: 3 });
    expect(onPreview).toHaveBeenLastCalledWith(expect.objectContaining({ visualizations: [expect.objectContaining({ label: '1', origin: 'e2', target: 'e4' })] }));
    fireEvent.click(screen.getByText('Preview candidate 1'));
    expect(onPreview).toHaveBeenLastCalledWith(expect.objectContaining({ fen: linePosition(fen, ['e2e4'], 1) }));
    fireEvent.click(screen.getByText('Next step'));
    expect(screen.getByText('Step 2 of 2')).toBeTruthy();
    fireEvent.click(screen.getByText('Return to game position'));
    expect(onPreview).toHaveBeenLastCalledWith(expect.objectContaining({ fen }));
    expect(submit).not.toHaveBeenCalled();
  });
  it('discards a delayed response after the actual position changes', async () => {
    const { promise, resolve } = Promise.withResolvers<PositionInsight>();
    vi.spyOn(ApiClient, 'getPositionInsight').mockReturnValue(promise);
    const onPreview = vi.fn();
    const view = render(<HintPanel playerId="p" fen={fen} onPreview={onPreview} />);
    fireEvent.click(screen.getByText('Hint'));
    view.rerender(<HintPanel playerId="p" fen={linePosition(fen, ['e2e4'], 1)} onPreview={onPreview} />);
    await act(async () => resolve(insight));
    await waitFor(() => expect(screen.queryByText('engine preference')).toBeNull());
    expect(onPreview).toHaveBeenLastCalledWith(null);
  });
  it('shows request errors and permits retry', async () => {
    vi.spyOn(ApiClient, 'getPositionInsight').mockRejectedValue(new Error('Engine busy'));
    render(<HintPanel playerId="p" fen={fen} onPreview={vi.fn()} />);
    fireEvent.click(screen.getByText('Hint'));
    expect(await screen.findByRole('alert')).toHaveTextContent('Engine busy');
    expect(screen.getByText('Hint')).not.toBeDisabled();
  });
});
