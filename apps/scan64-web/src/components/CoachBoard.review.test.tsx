import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import { CoachBoard } from './CoachBoard';

const board = vi.hoisted(() => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }));
vi.mock('chessground', () => ({ Chessground: () => board }));
beforeEach(() => { vi.clearAllMocks(); });
afterEach(() => { vi.restoreAllMocks(); });

describe('CoachBoard controlled review selection', () => {
  it('keeps the full selectable SAN list while selection is parent-controlled, without reparsing PGN', () => {
    const chess = new Chess();
    const initialFen = chess.fen();
    chess.loadPgn('1. f3 e5 2. g4 Qh4#');
    const moves = chess.history({ verbose: true });
    const onSelection = vi.fn();
    const loadPgn = vi.spyOn(Chess.prototype, 'loadPgn');
    const view = render(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={0} onSelectedPlyChange={onSelection} disabled />);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: initialFen, lastMove: [] }));
    expect(screen.getByRole('button', { name: '2... Qh4#' })).toBeInTheDocument();
    loadPgn.mockClear();
    fireEvent.click(screen.getByRole('button', { name: '2... Qh4#' }));
    expect(onSelection).toHaveBeenCalledWith(4);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: initialFen }));
    view.rerender(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={4} onSelectedPlyChange={onSelection} disabled />);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: moves[3].after, lastMove: ['d8', 'h4'] }));
    expect(screen.getByRole('button', { name: '2... Qh4#' })).toHaveAttribute('aria-current', 'step');
    expect(loadPgn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Initial position' }));
    expect(onSelection).toHaveBeenLastCalledWith(0);
  });

  it('replays custom-FEN Black starts and exact promotion plies', () => {
    const initialFen = '7k/8/8/8/8/8/p7/7K b - - 0 12';
    const chess = new Chess(initialFen);
    const move = chess.move('a2a1n');
    const view = render(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={0} orientation="black" disabled />);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: initialFen, orientation: 'black' }));
    expect(screen.getByRole('button', { name: /12\.\.\. a1=N/ })).toBeInTheDocument();
    view.rerender(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={1} orientation="black" disabled />);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: move.after, lastMove: ['a2', 'a1'] }));
  });

  it('identifies repeated positions by ply rather than FEN', () => {
    const chess = new Chess();
    const initialFen = chess.fen();
    chess.loadPgn('1. Nf3 Nf6 2. Ng1 Ng8');
    const view = render(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={0} disabled />);
    view.rerender(<CoachBoard fen={initialFen} pgn={chess.pgn()} selectedPly={4} disabled />);
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: chess.fen(), lastMove: ['f6', 'g8'] }));
    expect(screen.getByRole('button', { name: '2... Ng8' })).toHaveAttribute('aria-current', 'step');
    expect(screen.getByRole('button', { name: 'Initial position' })).not.toHaveAttribute('aria-current');
  });
});
