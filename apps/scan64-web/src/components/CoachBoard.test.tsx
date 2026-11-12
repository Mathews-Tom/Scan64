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

  it.each(['e2e4', 'e4'])('submits keyboard notation %s through the same UCI callback', async text => {
    const onMove = vi.fn();
    render(<CoachBoard fen={new Chess().fen()} onMove={onMove} />);
    const input = screen.getByRole('textbox', { name: 'Move (UCI or SAN)' });
    fireEvent.change(input, { target: { value: text } });
    await act(async () => { fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' })); });
    expect(onMove).toHaveBeenCalledExactlyOnceWith('e2e4');
  });

  it('rejects illegal entry without a callback and retains input focus', () => {
    const onMove = vi.fn();
    render(<CoachBoard fen={new Chess().fen()} onMove={onMove} />);
    const input = screen.getByRole('textbox', { name: 'Move (UCI or SAN)' });
    fireEvent.change(input, { target: { value: 'e2e5' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    expect(onMove).not.toHaveBeenCalled();
    expect(input).toHaveFocus();
    expect(input).toHaveValue('e2e5');
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a legal move');
  });

  it('focuses the promotion picker and cancels with Escape back to move entry', () => {
    const onMove = vi.fn();
    render(<CoachBoard fen="8/4P3/8/8/8/8/8/k6K w - - 0 1" onMove={onMove} />);
    const input = screen.getByRole('textbox', { name: 'Move (UCI or SAN)' });
    input.focus();
    fireEvent.change(input, { target: { value: 'e7e8' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    expect(screen.getByRole('button', { name: 'Promote to queen' })).toHaveFocus();
    expect(onMove).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole('button', { name: 'Promote to queen' }), { key: 'Escape' });
    expect(input).toHaveFocus();
    expect(screen.queryByRole('group', { name: 'Choose promotion piece' })).not.toBeInTheDocument();
  });

  it('shares busy and disabled guards across entry and pointer moves', async () => {
    const { promise, resolve: finish } = Promise.withResolvers<void>();
    const onMove = vi.fn(() => promise);
    const rendered = render(<CoachBoard fen={new Chess().fen()} onMove={onMove} />);
    const form = screen.getByRole('form', { name: 'Enter chess move' });
    fireEvent.change(screen.getByRole('textbox', { name: 'Move (UCI or SAN)' }), { target: { value: 'e4' } });
    fireEvent.submit(form);
    await move('d2', 'd4');
    fireEvent.submit(form);
    expect(onMove).toHaveBeenCalledTimes(1);
    await act(async () => { finish(); });
    rendered.rerender(<CoachBoard fen={new Chess().fen()} disabled onMove={onMove} />);
    fireEvent.submit(form);
    await move('d2', 'd4');
    expect(onMove).toHaveBeenCalledTimes(1);
  });

  it('exposes only position and legal choices, not hint or solution candidates', () => {
    render(<CoachBoard fen={new Chess().fen()} orientation="black" onMove={vi.fn()} />);
    expect(screen.getByRole('status')).toHaveTextContent('White to move.');
    expect(screen.getByText('e1: White king')).toBeInTheDocument();
    expect(screen.getByText('e8: Black king')).toBeInTheDocument();
    expect(screen.getByText(/Board viewed from Black/)).toBeInTheDocument();
    expect(screen.getByTestId('coach-board')).toHaveAttribute('aria-hidden', 'true');
    expect(screen.queryByText(/best move|solution|engine candidate/i)).not.toBeInTheDocument();
  });

  it('keeps controlled replay read-only for both entry and pointer', async () => {
    const onMove = vi.fn();
    render(<CoachBoard fen={new Chess().fen()} pgn="1. e4 e5 *" selectedPly={1} onMove={onMove} />);
    expect(screen.queryByRole('textbox', { name: 'Move (UCI or SAN)' })).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Review position 1. Black to move.');
    await move('e7', 'e5');
    expect(onMove).not.toHaveBeenCalled();
  });
});

describe('accessible board announcements and focus', () => {
  it('announces a committed player move and reply together after the callback completes', async () => {
    const initial = new Chess().fen();
    const game = new Chess();
    const { promise, resolve } = Promise.withResolvers<void>();
    const onMove = vi.fn(() => promise);
    const view = render(<CoachBoard fen={initial} onMove={onMove} />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Move (UCI or SAN)' }), { target: { value: 'e4' } });
    fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    game.move({ from: 'e2', to: 'e4' });
    view.rerender(<CoachBoard fen={game.fen()} pgn={game.pgn()} onMove={onMove} />);
    expect(screen.getByRole('status')).toHaveTextContent('Submitting move.');
    game.move({ from: 'e7', to: 'e5' });
    view.rerender(<CoachBoard fen={game.fen()} pgn={game.pgn()} onMove={onMove} />);
    await act(async () => resolve());
    expect(screen.getByRole('status')).toHaveTextContent('White played e4. Black played e5. White to move.');
    expect(screen.getByRole('status').textContent?.match(/played e4/g)).toHaveLength(1);
  });

  it('does not replay the full move history after preview or loading an opening', () => {
    const game = new Chess();
    game.loadPgn('1. e4 e5 2. Nf3 Nc6');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={new Chess().fen()} onMove={onMove} />);
    view.rerender(<CoachBoard fen={game.fen()} pgn={game.pgn()} onMove={onMove} />);
    expect(screen.getByRole('status')).toHaveTextContent('Position loaded. Last move: Black played Nc6.');
    expect(screen.getByRole('status')).not.toHaveTextContent('played e4');
    view.rerender(<CoachBoard fen={new Chess().fen()} preview onMove={onMove} />);
    expect(screen.getByRole('status')).toHaveTextContent('Line preview.');
    expect(screen.getByRole('textbox', { name: 'Move (UCI or SAN)' })).toHaveAttribute('readonly');
    view.rerender(<CoachBoard fen={game.fen()} pgn={game.pgn()} onMove={onMove} />);
    expect(screen.getByRole('status')).not.toHaveTextContent('played');
  });

  it('restores entry focus when promotion was opened from the submit button', async () => {
    const onMove = vi.fn();
    render(<CoachBoard fen="8/4P3/8/8/8/8/8/k6K w - - 0 1" onMove={onMove} />);
    const input = screen.getByRole('textbox', { name: 'Move (UCI or SAN)' });
    const submit = screen.getByRole('button', { name: 'Make move' });
    fireEvent.change(input, { target: { value: 'e7e8' } });
    submit.focus();
    fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    fireEvent.keyDown(screen.getByRole('button', { name: 'Promote to queen' }), { key: 'Escape' });
    expect(input).toHaveFocus();
    submit.focus();
    fireEvent.submit(screen.getByRole('form', { name: 'Enter chess move' }));
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Promote to queen' })));
    expect(input).toHaveFocus();
    expect(onMove).toHaveBeenCalledExactlyOnceWith('e7e8q');
  });
});
