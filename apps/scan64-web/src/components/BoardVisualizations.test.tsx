import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { BoardVisualizations, VisualizationDescriptions } from './BoardVisualizations';
import type { Visualization } from '../api/types';

const fen = '4k3/8/8/8/8/8/4P3/4K3 w - - 0 1';
afterEach(cleanup);

describe('Board annotation alternatives', () => {
  it('hides graphical decoration and supplies one coordinate-based text equivalent', () => {
    const visualizations: Visualization[] = [
      { command: 'draw_arrow', description: 'Advance the pawn', origin: 'e2', target: 'e4', label: '1' },
      { command: 'highlight_piece', description: 'Inspect the pawn', square: 'e2' },
      { command: 'draw_defence_map', description: 'Pawn defenders', square: 'e2' },
    ];
    const { container } = render(<>
      <BoardVisualizations fen={fen} orientation="black" visualizations={visualizations} />
      <VisualizationDescriptions fen={fen} visualizations={visualizations} />
    </>);
    expect(container.querySelector('svg')).toHaveAttribute('aria-hidden', 'true');
    expect(container.querySelector('svg')).toHaveAttribute('focusable', 'false');
    const annotations = screen.getByRole('list', { name: 'Board annotations' });
    expect(annotations).toHaveTextContent('1. Advance the pawn (e2 to e4)');
    expect(annotations).toHaveTextContent('Inspect the pawn (e2)');
    expect(annotations).toHaveTextContent('e2: defended by e1.');
    expect(screen.getAllByText(/Advance the pawn/)).toHaveLength(1);
  });

  it('numbers only arrows when highlights precede or separate them', () => {
    render(<VisualizationDescriptions fen={fen} visualizations={[
      { command: 'highlight_piece', description: 'Inspect the pawn', square: 'e2' },
      { command: 'draw_arrow', description: 'Advance the pawn', origin: 'e2', target: 'e4' },
      { command: 'highlight_square', description: 'Inspect the centre', square: 'e4' },
      { command: 'draw_arrow', description: 'Prepare the pawn', origin: 'e2', target: 'e3' },
    ]} />);
    const annotations = screen.getByRole('list', { name: 'Board annotations' });
    expect(annotations).toHaveTextContent('1. Advance the pawn (e2 to e4)');
    expect(annotations).toHaveTextContent('2. Prepare the pawn (e2 to e3)');
    expect(annotations).not.toHaveTextContent('1. Inspect the pawn');
  });

  it('retains server ordinals once and names every annotation kind without relying on colour', () => {
    const visualizations: Visualization[] = [
      { command: 'draw_arrow', description: '1. Nf3', origin: 'g1', target: 'f3' },
      { command: 'draw_arrow', description: '2. Nc3', origin: 'b1', target: 'c3' },
      { command: 'draw_arrow', description: '3. Nf6', origin: 'g8', target: 'f6' },
    ];
    const start = 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1';
    const { container } = render(<>
      <BoardVisualizations fen={start} orientation="white" visualizations={visualizations} />
      <VisualizationDescriptions fen={start} visualizations={visualizations} />
    </>);
    const annotations = screen.getByRole('list', { name: 'Board annotations' });
    expect(annotations).toHaveTextContent('Main move: 1. Nf3 (g1 to f3)');
    expect(annotations).toHaveTextContent('Alternative: 2. Nc3 (b1 to c3)');
    expect(annotations).toHaveTextContent('Threat: 3. Nf6 (g8 to f6)');
    expect(annotations).not.toHaveTextContent('1. 1.');
    expect([...container.querySelectorAll('svg line')].map(line => line.getAttribute('stroke'))).toEqual([
      'var(--annotation-best)', 'var(--annotation-alternative)', 'var(--annotation-threat)',
    ]);
  });

  it('does not repeat non-graphical descriptions or expose unrevealed annotations', () => {
    const { rerender } = render(<VisualizationDescriptions fen={fen} visualizations={[]} />);
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    rerender(<VisualizationDescriptions fen={fen} visualizations={[
      { command: 'animate_line', description: 'A separate line preview', moves: ['e2e4'] },
    ]} />);
    expect(screen.queryByText('A separate line preview')).not.toBeInTheDocument();
  });
});
