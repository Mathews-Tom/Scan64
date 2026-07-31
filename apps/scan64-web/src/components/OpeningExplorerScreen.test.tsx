import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { OpeningExplorerScreen } from './OpeningExplorerScreen';
import { ApiClient } from '../api/client';

type MoveHandler = (orig: string, dest: string) => void;

const chessgroundMock = vi.hoisted(() => ({
  after: undefined as MoveHandler | undefined,
  set: vi.fn(),
}));

vi.mock('chessground', () => ({
  Chessground: (
    _element: Element,
    config: { movable: { events?: { after?: MoveHandler } } },
  ) => {
    chessgroundMock.after = config.movable.events?.after;
    return {
      set: (update: { movable?: { events?: { after?: MoveHandler } } }) => {
        const after = update.movable?.events?.after;
        if (after) chessgroundMock.after = after;
        chessgroundMock.set(update);
      },
    };
  },
}));

beforeEach(() => {
  chessgroundMock.after = undefined;
  chessgroundMock.set.mockReset();
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

  it('surfaces a rejected board move and resynchronizes Chessground', async () => {
    render(<OpeningExplorerScreen />);

    await waitFor(() => {
      expect(chessgroundMock.after).toBeDefined();
    });
    const after = chessgroundMock.after;
    if (!after) throw new Error('Chessground move handler was not registered');

    await act(async () => {
      after('e2', 'e8');
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid move');
    expect(chessgroundMock.set).toHaveBeenCalledWith(
      expect.objectContaining({
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1',
      }),
    );
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


