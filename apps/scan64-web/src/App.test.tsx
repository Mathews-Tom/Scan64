import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import App from './App';
import { ApiClient } from './api/client';
import type { LessonSpec } from './api/types';

vi.mock('./api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./api/client')>();
  return {
    ...actual,
    ApiClient: {
      ...actual.ApiClient,
      getTrainingSession: vi.fn(),
      getPlayerProgress: vi.fn(),
      getPlayerEvidence: vi.fn(),
      getPlayerPatterns: vi.fn(),
      getPositions: vi.fn().mockResolvedValue([]),
    },
  };
});

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.clearAllMocks();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('App', () => {
  it('renders welcome message by default', () => {
    render(<App />);
    expect(screen.getByText('Welcome to Scan64')).toBeInTheDocument();
  });

  it('navigates to play screen and updates the URL', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Play Game'));

    expect(window.location.pathname).toBe('/play');
    expect(screen.getByTestId('play-screen')).toBeInTheDocument();
  });

  it('renders a directly addressed screen from its URL', () => {
    window.history.replaceState({}, '', '/play');

    render(<App />);

    expect(screen.getByTestId('play-screen')).toBeInTheDocument();
  });

  it('renders a game analysis screen from its deep-link URL', async () => {
    window.history.replaceState({}, '', '/games/00000000-0000-0000-0000-000000000001/analysis');

    render(<App />);

    await waitFor(() => expect(ApiClient.getPositions).toHaveBeenCalledWith('00000000-0000-0000-0000-000000000001'));
    expect(screen.getByRole('heading', { name: 'Analysis Board' })).toBeInTheDocument();
  });


  it('renders not found for a malformed encoded game-analysis URL', () => {
    window.history.replaceState({}, '', '/games/%/analysis');

    render(<App />);

    expect(screen.getByTestId('not-found')).toHaveTextContent('Page not found');
  });

  it('renders not found for a non-UUID game-analysis URL', () => {
    window.history.replaceState({}, '', '/games/not-a-uuid/analysis');

    render(<App />);

    expect(screen.getByTestId('not-found')).toHaveTextContent('Page not found');
    expect(ApiClient.getPositions).not.toHaveBeenCalled();
  });
  it('follows browser Back navigation', async () => {
    render(<App />);
    fireEvent.click(screen.getByText('Play Game'));

    window.history.back();

    await waitFor(() => {
      expect(screen.getByText('Welcome to Scan64')).toBeInTheDocument();
    });
  });

  it('does not add a history entry for same-path navigation', () => {
    render(<App />);
    const pushState = vi.spyOn(window.history, 'pushState');

    fireEvent.click(within(screen.getByRole('navigation')).getByText('Import PGN'));
    fireEvent.click(within(screen.getByRole('navigation')).getByText('Import PGN'));

    expect(pushState).toHaveBeenCalledTimes(1);
  });

  it('renders an explicit missing-route state', () => {
    window.history.replaceState(null, '', '/missing');

    render(<App />);

    expect(screen.getByTestId('not-found')).toHaveTextContent('Page not found');
  });

  it('navigates to import screen', () => {
    render(<App />);
    fireEvent.click(screen.getByText('Import PGN'));
    expect(screen.getByTestId('pgn-import')).toBeInTheDocument();
  });

  it('navigates to daily training screen and loads a lesson', async () => {
    const mockLesson: LessonSpec = {
      schema_version: '1.0',
      lesson_id: 'lesson-1',
      source: {
        kind: 'position',
        fen: 'rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1'
      },
      diagnosis: {
        primary: 'tactics.fork',
        secondary: [],
        confidence: 0.9,
        evidence_refs: []
      },
      objective: {
        type: 'find_best_move',
        instruction: 'Find the winning tactic.'
      },
      interaction: {} as unknown as LessonSpec['interaction'],
      hints: [],
      explanation: {} as unknown as LessonSpec['explanation'],
      verification: {} as unknown as LessonSpec['verification'],
      mastery: {} as unknown as LessonSpec['mastery']
    };

    vi.spyOn(ApiClient, 'getTrainingSession').mockResolvedValue({
      session_id: 'session-1',
      lessons: [mockLesson],
    });

    render(<App />);
    fireEvent.click(screen.getByText('Daily Training'));
    
    // Should show loading then the instruction
    expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
    
    await waitFor(() => {
      expect(screen.getByTestId('lesson-instruction')).toBeInTheDocument();
    });
    
    expect(screen.getByText('Find the winning tactic.')).toBeInTheDocument();
  });
});

it('navigates to profile screen', async () => {
  vi.spyOn(ApiClient, 'getPlayerProgress').mockResolvedValue({
    player_id: 'player-1',
    skills: [],
  });
  vi.spyOn(ApiClient, 'getPlayerEvidence').mockResolvedValue({
    player_id: 'player-1',
    evidence_items: [],
  });
  vi.spyOn(ApiClient, 'getPlayerPatterns').mockResolvedValue({
    player_id: 'player-1',
    minimum_occurrences: 3,
    status: 'insufficient_data',
    recurring_diagnoses: [],
  });

  render(<App />);
  fireEvent.click(screen.getByText('Profile'));

  await waitFor(() => {
    expect(screen.getByTestId('profile-screen')).toBeInTheDocument();
  });

  expect(screen.getByText(/Player Profile/i)).toBeInTheDocument();
});
