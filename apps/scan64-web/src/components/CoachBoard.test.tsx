import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import type { Key } from 'chessground/types';
import { CoachBoard } from './CoachBoard';

type MoveHandler = (from: Key, to: Key) => void;
const board = vi.hoisted(() => ({ after: undefined as MoveHandler | undefined,
  set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(), free: undefined as boolean | undefined }));
vi.mock('chessground', () => ({ Chessground: (_: Element,
  config: { movable: { events: { after: MoveHandler }; free: boolean } }) => {
  board.after = config.movable.events.after;
  board.free = config.movable.free;
  return board;
} }));

async function move(from: Key, to: Key) {
  await waitFor(() => expect(board.after).toBeDefined());
  await act(async () => { board.after!(from, to); });
}

describe('CoachBoard', () => {
  beforeEach(() => { vi.clearAllMocks(); board.after = undefined; });

  it('resets rejected lesson attempts without remounting', async () => {
    const fen = new Chess().fen();
    const onMove = vi.fn();
    const rendered = render(<CoachBoard fen={fen} resetAfterAttempt onMove={onMove} />);
    await move('e2', 'e4');
    rendered.rerender(<CoachBoard fen={fen} resetAfterAttempt disabled onMove={onMove} />);
    rendered.rerender(<CoachBoard fen={fen} resetAfterAttempt onMove={onMove} />);
    await move('e2', 'e4');
    expect(onMove).toHaveBeenCalledTimes(2);
    expect(onMove).toHaveBeenLastCalledWith('e2e4');
    expect(board.destroy).not.toHaveBeenCalled();
    expect(board.free).toBe(false);
  });

  it('restores the legal position after an invalid move', async () => {
    const fen = new Chess().fen();
    const onMove = vi.fn();
    render(<CoachBoard fen={fen} onMove={onMove} />);
    await move('e2', 'e5');
    expect(onMove).not.toHaveBeenCalled();
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen }));
  });

  it.each([['queen', 'q'], ['rook', 'r'], ['bishop', 'b'], ['knight', 'n']])(
    'offers every promotion and submits %s with its UCI suffix', async (name, suffix) => {
      const onMove = vi.fn();
      render(<CoachBoard fen="8/P7/8/8/8/8/8/k6K w - - 0 1" onMove={onMove} />);
      await move('a7', 'a8');
      for (const piece of ['queen', 'rook', 'bishop', 'knight']) {
        expect(screen.getByRole('button', { name: `Promote to ${piece}` })).toBeInTheDocument();
      }
      expect(onMove).not.toHaveBeenCalled();
      await act(async () => { fireEvent.click(screen.getByRole('button', { name: `Promote to ${name}` })); });
      expect(onMove).toHaveBeenCalledWith(`a7a8${suffix}`);
    },
  );

  it('updates SAN and last move after each pair and supports black orientation', () => {
    const chess = new Chess();
    const rendered = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} orientation="black" onMove={vi.fn()} />);
    chess.move('e4'); chess.move('e5');
    rendered.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} orientation="black" onMove={vi.fn()} />);
    expect(screen.getByTestId('san-move-list')).toHaveTextContent('1. e4');
    expect(screen.getByTestId('san-move-list')).toHaveTextContent('1... e5');
    chess.move('Nf3'); chess.move('Nc6');
    rendered.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} orientation="black" onMove={vi.fn()} />);
    expect(screen.getByTestId('san-move-list')).toHaveTextContent('2. Nf3');
    expect(screen.getByTestId('san-move-list')).toHaveTextContent('2... Nc6');
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black', lastMove: ['b8', 'c6'] }));
  });
});
