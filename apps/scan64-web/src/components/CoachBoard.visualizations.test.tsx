import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Chess } from 'chess.js';
import { CoachBoard } from './CoachBoard';
import { BoardVisualizations } from './BoardVisualizations';

const ground = vi.hoisted(() => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }));
vi.mock('chessground', () => ({ Chessground: vi.fn(() => ground) }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });
describe('board visualizations', () => {
  it('draws numbered arrows and square, piece and defence overlays', () => {
    const { container } = render(<BoardVisualizations fen={new Chess().fen()} orientation="white" visualizations={[
      { command: 'draw_arrow', description: 'First', origin: 'e2', target: 'e4', label: '1' },
      { command: 'draw_arrow', description: 'Second', origin: 'd2', target: 'd4' },
      { command: 'highlight_square', description: 'Square', square: 'e4' },
      { command: 'highlight_piece', description: 'Piece', square: 'g1' },
      { command: 'draw_defence_map', description: 'Defended', squares: ['e4', 'd4'] },
    ]} />);
    expect(screen.getByLabelText('1: First')).toBeTruthy();
    expect(screen.getByLabelText('2: Second')).toBeTruthy();
    expect(container.querySelectorAll('line')).toHaveLength(2);
    expect(container.querySelectorAll('rect')).toHaveLength(1);
    expect(screen.getByLabelText('Piece').querySelector('circle')).toBeTruthy();
  });
  it('draws friendly defenders with orientation-correct coordinates', () => {
    const fen = '4k3/8/8/8/4P3/3P4/8/4K3 w - - 0 1';
    const visualizations = [{ command: 'draw_defence_map', description: 'Defence', square: 'e4' }];
    const view = render(<BoardVisualizations fen={fen} orientation="white" visualizations={visualizations} />);
    const arrow = screen.getByLabelText('Defender d3 to e4');
    expect(arrow.getAttribute('x1')).toBe('43.75');
    expect(arrow.getAttribute('y1')).toBe('68.75');
    expect(arrow.getAttribute('x2')).toBe('56.25');
    view.rerender(<BoardVisualizations fen={fen} orientation="black" visualizations={visualizations} />);
    expect(screen.getByLabelText('Defender d3 to e4').getAttribute('x1')).toBe('56.25');
    expect(screen.getByLabelText('Defender d3 to e4').getAttribute('y1')).toBe('31.25');
  });
  it('renders no defence arrows for empty or undefended squares', () => {
    const { container } = render(<BoardVisualizations fen="4k3/8/8/8/4P3/8/8/4K3 w - - 0 1"
      orientation="white" visualizations={[{ command: 'draw_defence_map', description: 'Empty', squares: ['d4', 'e4'] }]} />);
    expect(container.querySelectorAll('line')).toHaveLength(0);
  });
  it('excludes a pinned defender that cannot legally recapture', () => {
    const { container } = render(<BoardVisualizations fen="k3r3/8/8/8/2P5/4N3/8/4K3 w - - 0 1"
      orientation="white" visualizations={[{ command: 'draw_defence_map', description: 'Pinned', square: 'c4' }]} />);
    expect(container.querySelectorAll('line')).toHaveLength(0);
  });
  it('labels all three candidate arrows separately even when their origin is shared', () => {
    const { container } = render(<BoardVisualizations fen={new Chess().fen()} orientation="white" visualizations={[
      { command: 'draw_arrow', description: 'First', origin: 'g1', target: 'f3', label: '1' },
      { command: 'draw_arrow', description: 'Second', origin: 'g1', target: 'h3', label: '2' },
      { command: 'draw_arrow', description: 'Third', origin: 'b1', target: 'c3', label: '3' },
    ]} />);
    expect([...container.querySelectorAll('text')].map(text => text.textContent)).toEqual(['1', '2', '3']);
    expect(['1: First', '2: Second', '3: Third'].map(label => screen.getByLabelText(label))).toHaveLength(3);
    const positions = [...container.querySelectorAll('text')].map(text => `${text.getAttribute('x')},${text.getAttribute('y')}`);
    expect(new Set(positions).size).toBe(3);
  });
  it('shows description paragraphs only for commands not rendered on the board', () => {
    const { container } = render(<CoachBoard fen={new Chess().fen()} onMove={vi.fn()} visualizations={[
      { command: 'draw_arrow', description: 'Arrow description', origin: 'g1', target: 'f3' },
      { command: 'highlight_square', description: 'Square description', square: 'e4' },
      { command: 'highlight_piece', description: 'Piece description', square: 'g1' },
      { command: 'draw_defence_map', description: 'Defence description', square: 'e2' },
      { command: 'animate_line', description: 'Animation description', moves: ['e2e4'] },
      { command: 'future_command', description: 'Unsupported description' },
    ]} />);
    expect([...container.querySelectorAll('p')].map(paragraph => paragraph.textContent)).toEqual(['Animation description', 'Unsupported description']);
    expect(screen.getByRole('button', { name: 'Next line move' })).toBeTruthy();
  });
  it('previews animate_line without moving and retains unknown descriptions', async () => {
    const fen = new Chess().fen();
    const onMove = vi.fn();
    render(<CoachBoard fen={fen} onMove={onMove} visualizations={[
      { command: 'animate_line', description: 'Explore', moves: ['e2e4', 'e7e5'] },
      { command: 'future_command', description: 'Readable fallback' },
    ]} />);
    expect(screen.getByText('Readable fallback')).toBeTruthy();
    fireEvent.click(screen.getByText('Next line move'));
    const expected = new Chess(); expected.move('e4');
    await waitFor(() => expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: expected.fen() })));
    fireEvent.click(screen.getByText('Show full line')); expected.move('e5');
    await waitFor(() => expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen: expected.fen() })));
    fireEvent.click(screen.getByText('Return to original position'));
    await waitFor(() => expect(ground.set).toHaveBeenLastCalledWith(expect.objectContaining({ fen })));
    expect(onMove).not.toHaveBeenCalled();
  });
});
