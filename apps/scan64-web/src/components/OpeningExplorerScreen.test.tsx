import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OpeningExplorerScreen } from './OpeningExplorerScreen';
import { ApiClient } from '../api/client';

vi.mock('chessground', () => ({
  Chessground: () => ({ set: vi.fn(), destroy: vi.fn(), redrawAll: vi.fn() }),
}));

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValue({
    session_id: 'opening-study-1',
    lessons: [],
  });
});

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

  it('announces rejected keyboard moves, retains focus and leaves the position unchanged', () => {
    render(<OpeningExplorerScreen />);
    const entry = screen.getByLabelText('Move (UCI or SAN)');
    fireEvent.change(entry, { target: { value: 'e2e8' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Enter a legal move');
    expect(entry).toHaveFocus();
    expect(screen.getByText('e2: White pawn')).toBeInTheDocument();
    expect(screen.getByTestId('chessground-board-san-move-list')).toBeEmptyDOMElement();
  });

  it('shares the selected opening position and move history with accessible keyboard play', async () => {
    render(<OpeningExplorerScreen />);
    fireEvent.click(screen.getByRole('button', { name: 'Italian Game' }));
    expect(screen.getByText('c4: White bishop')).toBeInTheDocument();
    expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('3. Bc4');
    fireEvent.change(screen.getByLabelText('Move (UCI or SAN)'), { target: { value: 'g8f6' } });
    fireEvent.click(screen.getByRole('button', { name: 'Make move' }));
    await waitFor(() => expect(screen.getByTestId('chessground-board-san-move-list')).toHaveTextContent('3... Nf6'));
    expect(screen.getByText('f6: Black knight')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: "Queen's Gambit" }));
    expect(screen.getByText('c4: White pawn')).toBeInTheDocument();
    expect(screen.queryByText('f6: Black knight')).not.toBeInTheDocument();
  });

  it('records an opening mission through the server-side attempt endpoint', async () => {
    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValue({
      session_id: 'opening-study-1',
      lessons: [],
    });
    const recordAttempt = vi.spyOn(ApiClient, 'recordLessonAttempt').mockResolvedValue({
      id: 'opening-attempt-1',
      success: true,
      grading_status: 'verified',
      profile_update_result: 'not_applicable',
    });
    render(<OpeningExplorerScreen />);

    fireEvent.click(screen.getByText('Italian Game'));
    const recordButton = await screen.findByRole('button', { name: 'Record mission' });
    fireEvent.click(recordButton);

    await waitFor(() => {
      expect(recordAttempt).toHaveBeenCalledWith({
        session_id: 'opening-study-1',
        lesson_id: 'opening-mission:italian_dev_minor',
        source_kind: 'opening_mission',
        elapsed_ms: 0,
        hints_used: 0,
      });
    });
    expect(await screen.findByRole('button', { name: 'Recorded' })).toBeInTheDocument();
  });
});


