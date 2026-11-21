import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import type { Config } from 'chessground/config';
import type { Key } from 'chessground/types';
import { CoachBoard } from './CoachBoard';

const ground = vi.hoisted(() => ({
  config: {} as Config,
  state: { premovable: { current: undefined as [Key, Key] | undefined }, drawable: { shapes: [] } },
  set: vi.fn(), cancelPremove: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn(),
}));
vi.mock('chessground', () => ({ Chessground: (_: Element, config: Config) => {
  ground.config = config;
  ground.cancelPremove.mockImplementation(() => {
    ground.state.premovable.current = undefined;
    config.premovable?.events?.unset?.();
  });
  return ground;
} }));
vi.mock('../boardSounds', () => ({ playMoveSound: vi.fn(), playBoardSound: vi.fn() }));

function queue(from: Key, to: Key) {
  act(() => {
    ground.state.premovable.current = [from, to];
    ground.config.premovable?.events?.set?.(from, to);
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  ground.state.premovable.current = undefined;
  vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(true);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe('Board controls', () => {
  it('flips coordinates and the readable viewing side without changing pieces or server annotations', () => {
    const fen = new Chess().fen();
    render(<CoachBoard flippable fen={fen} disabled visualizations={[
      { command: 'draw_arrow', origin: 'e2', target: 'e4', description: 'Coach move' },
    ]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Flip board' }));
    expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ orientation: 'black', fen }));
    expect(screen.getByRole('button', { name: 'Flip board' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText(/Board viewed from Black/)).toBeInTheDocument();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Board annotations', hidden: true })).toHaveTextContent('Coach move');
    expect(ground.config.drawable).toMatchObject({ enabled: true, visible: true, eraseOnClick: true });
    expect(ground.config.premovable?.enabled).toBe(false);
  });

  it('holds a premove during the bot turn and sends it only after a legal reply', async () => {
    const chess = new Chess(); chess.move('e4');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ movable: expect.objectContaining({ color: 'white' }), premovable: { enabled: true } }));
    queue('g1', 'f3');
    expect(screen.getByRole('button', { name: 'Cancel premove' })).toBeInTheDocument();
    expect(onMove).not.toHaveBeenCalled();
    chess.move('e5');
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" onMove={onMove} />);
    await waitFor(() => expect(onMove).toHaveBeenCalledExactlyOnceWith('g1f3'));
    expect(ground.state.premovable.current).toBeUndefined();
  });

  it('clears a legal premove after the player move is rejected without a bot reply', () => {
    const chess = new Chess(); chess.move('e4');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    queue('g1', 'f3');
    chess.undo();
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" onMove={onMove} />);
    expect(onMove).not.toHaveBeenCalled();
    expect(ground.state.premovable.current).toBeUndefined();
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
  });

  it.each(['1. d4 *', '1. d4 d5 *'])('cancels a premove when canonical history replaces its source: %s', pgn => {
    const chess = new Chess(); chess.move('e4');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    queue('g1', 'f3');
    chess.loadPgn(pgn);
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
    expect(ground.state.premovable.current).toBeUndefined();
    if (chess.turn() === 'b') chess.move('d5');
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" onMove={onMove} />);
    expect(onMove).not.toHaveBeenCalled();
  });

  it('cancels a premove when a FEN-only source is replaced by a different initial position', () => {
    const chess = new Chess('7k/8/8/8/8/8/8/KN6 b - - 0 1');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} premoveColor="white" disabled onMove={onMove} />);
    queue('b1', 'c3');
    chess.load('6k1/8/8/8/8/8/8/KN6 w - - 1 2');
    view.rerender(<CoachBoard fen={chess.fen()} premoveColor="white" onMove={onMove} />);
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
    expect(onMove).not.toHaveBeenCalled();
  });

  it('cancels a premove when equal-ply replacement history reaches the same FEN by a different move order', () => {
    const source = new Chess(); source.loadPgn('1. Nf3 Nf6 2. Nc3 *');
    const replacement = new Chess(); replacement.loadPgn('1. Nc3 Nf6 2. Nf3 *');
    expect(replacement.fen()).toBe(source.fen());
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={source.fen()} pgn={source.pgn()} premoveColor="white" disabled onMove={onMove} />);
    queue('e2', 'e4');
    view.rerender(<CoachBoard fen={replacement.fen()} pgn={replacement.pgn()} premoveColor="white" disabled onMove={onMove} />);
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
    replacement.move('d5');
    view.rerender(<CoachBoard fen={replacement.fen()} pgn={replacement.pgn()} premoveColor="white" onMove={onMove} />);
    expect(onMove).not.toHaveBeenCalled();
  });

  it('discards a premove made illegal by the reply', () => {
    const chess = new Chess(); chess.move('e4');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    queue('e4', 'd5');
    chess.move('e5');
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" onMove={onMove} />);
    expect(onMove).not.toHaveBeenCalled();
    expect(ground.state.premovable.current).toBeUndefined();
    expect(screen.queryByRole('button', { name: 'Cancel premove' })).not.toBeInTheDocument();
  });

  it.each(['button', 'escape', 'offline', 'interruption'])('cancels a pending premove via %s', method => {
    const chess = new Chess(); chess.move('e4');
    const onMove = vi.fn();
    const view = render(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" disabled onMove={onMove} />);
    queue('g1', 'f3');
    if (method === 'button') fireEvent.click(screen.getByRole('button', { name: 'Cancel premove' }));
    if (method === 'escape') fireEvent.keyDown(screen.getByRole('button', { name: 'Cancel premove' }), { key: 'Escape' });
    if (method === 'offline') act(() => { window.dispatchEvent(new Event('offline')); });
    if (method === 'interruption') view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} disabled onMove={onMove} />);
    expect(ground.state.premovable.current).toBeUndefined();
    chess.move('e5');
    view.rerender(<CoachBoard fen={chess.fen()} pgn={chess.pgn()} premoveColor="white" onMove={onMove} />);
    expect(onMove).not.toHaveBeenCalled();
  });

  it('does not enable premoves for lessons, puzzles or a read-only board', () => {
    render(<CoachBoard fen={new Chess().fen()} onMove={vi.fn()} />);
    expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ premovable: { enabled: false } }));
  });
});
