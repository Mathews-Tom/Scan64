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

  it('does not repeat non-graphical descriptions or expose unrevealed annotations', () => {
    const { rerender } = render(<VisualizationDescriptions fen={fen} visualizations={[]} />);
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
    rerender(<VisualizationDescriptions fen={fen} visualizations={[
      { command: 'animate_line', description: 'A separate line preview', moves: ['e2e4'] },
    ]} />);
    expect(screen.queryByText('A separate line preview')).not.toBeInTheDocument();
  });
});
