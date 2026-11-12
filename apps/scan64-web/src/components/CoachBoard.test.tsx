import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
  afterEach(() => { vi.restoreAllMocks(); });

  it('does not reparse unchanged PGN during board UI rerenders', () => {
    const onMove = vi.fn();
    const fen = new Chess().fen();
    const pgn = '1. e4 e5 *';
    const loadPgn = vi.spyOn(Chess.prototype, 'loadPgn');
    const rendered = render(<CoachBoard fen={fen} pgn={pgn} onMove={onMove} />);
    loadPgn.mockClear();
    rendered.rerender(<CoachBoard fen={fen} pgn={pgn} disabled orientation="black" onMove={onMove} />);
    expect(loadPgn).not.toHaveBeenCalled();
  });

  it('scopes SAN lists to each board and displays move numbers without list markers', () => {
    const chess = new Chess();
    chess.move('e4'); chess.move('e5');
    render(<>
      <CoachBoard fen={chess.fen()} pgn={chess.pgn()} testId="play-board" onMove={vi.fn()} />
      <CoachBoard fen={new Chess().fen()} testId="lesson-board" onMove={vi.fn()} />
    </>);
    const list = screen.getByTestId('play-board-san-move-list');
    expect(list).toHaveTextContent('1. e4');
    expect(list).toHaveTextContent('1... e5');
    expect(list).toHaveStyle({ listStyle: 'none', padding: '0px' });
    expect(screen.getByTestId('lesson-board-san-move-list')).toBeEmptyDOMElement();
  });

  it('exposes board readiness and marks disabled and promotion states busy', async () => {
    const onMove = vi.fn();
    const fen = '8/P7/8/8/8/8/8/k6K w - - 0 1';
    const rendered = render(<CoachBoard fen={fen} testId="lesson-board" onMove={onMove} />);
    expect(screen.getByTestId('lesson-board')).toHaveAttribute('aria-busy', 'false');
    rendered.rerender(<CoachBoard fen={fen} testId="lesson-board" disabled onMove={onMove} />);
    expect(screen.getByTestId('lesson-board')).toHaveAttribute('aria-busy', 'true');
    rendered.rerender(<CoachBoard fen={fen} testId="lesson-board" onMove={onMove} />);
    expect(screen.getByTestId('lesson-board')).toHaveAttribute('aria-busy', 'false');
    await move('a7', 'a8');
    expect(screen.getByTestId('lesson-board')).toHaveAttribute('aria-busy', 'true');
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Promote to rook' })); });
    expect(screen.getByTestId('lesson-board')).toHaveAttribute('aria-busy', 'false');
  });

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
    expect(screen.getByTestId('coach-board-san-move-list')).toHaveTextContent('1. e4');
    expect(screen.getByTestId('coach-board-san-move-list')).toHaveTextContent('1... e5');
    chess.move('Nf3'); chess.move('Nc6');
    rendered.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} orientation="black" onMove={vi.fn()} />);
    expect(screen.getByTestId('coach-board-san-move-list')).toHaveTextContent('2. Nf3');
    expect(screen.getByTestId('coach-board-san-move-list')).toHaveTextContent('2... Nc6');
    expect(board.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black', lastMove: ['b8', 'c6'] }));
  });
});
