import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OpeningExplorerScreen } from './OpeningExplorerScreen';

describe('OpeningExplorerScreen', () => {
  it('renders the opening explorer with seed families', () => {
    render(<OpeningExplorerScreen />);
    expect(screen.getByTestId('opening-explorer')).toBeInTheDocument();
    
    const families = ['Italian Game', "Queen's Gambit", 'Caro-Kann Defense'];
    families.forEach(name => {
      expect(screen.getByText(name)).toBeInTheDocument();
    });
  });

  it('loads family details when clicked', () => {
    render(<OpeningExplorerScreen />);
    
    const italianButton = screen.getByText('Italian Game');
    fireEvent.click(italianButton);
    
    expect(screen.getByTestId('family-details')).toBeInTheDocument();
    expect(screen.getByText(/Rapid development, central tension, king safety/)).toBeInTheDocument();
    expect(screen.getByText(/Develop both minor pieces before starting an attack/)).toBeInTheDocument();
    expect(screen.getByText(/minor_pieces_developed/)).toBeInTheDocument();
  });
});
