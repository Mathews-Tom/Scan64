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
      getPlayerProfile: vi.fn().mockResolvedValue({ player_id: 'player-1', rating: 1500, display_name: null }),
      getPlayerProgress: vi.fn(),
      getPlayerEvidence: vi.fn(),
      getPlayerPatterns: vi.fn(),
      getPositions: vi.fn().mockResolvedValue([]),
      getGameAnalysisStatus: vi.fn().mockResolvedValue({ status: 'not_analysed' }),
      getPlayerGames: vi.fn(),
      getWeeklyReport: vi.fn(),
      getFamousGames: vi.fn().mockResolvedValue([]),
      getCoachDashboard: vi.fn().mockResolvedValue({ coach_id: 'player-1', students: [] }),
    },
  };
});

beforeEach(() => {
  window.history.replaceState(null, '', '/');
  vi.clearAllMocks();
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'player-1');
  vi.mocked(ApiClient.getTrainingSession).mockResolvedValue({ session_id: 'study-1', lessons: [] });
  vi.mocked(ApiClient.getPlayerProgress).mockResolvedValue({ player_id: 'player-1', skills: [] });
  vi.mocked(ApiClient.getPlayerEvidence).mockResolvedValue({ player_id: 'player-1', evidence_items: [] });
  localStorage.setItem('scan64_player_token:player-1', 'test-token');
  vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({ items: [], next_cursor: null });
  vi.mocked(ApiClient.getWeeklyReport).mockResolvedValue({
    player_id: 'player-1', games_played: 0, active_concepts_observed: 0, active_mastery: [], top_recurring_diagnosis: null,
  });
  vi.mocked(ApiClient.getPlayerPatterns).mockResolvedValue({
    player_id: 'player-1', minimum_occurrences: 3, status: 'insufficient_data', recurring_diagnoses: [],
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('App', () => {
  it('renders Today by default for a stored identity', () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Today' })).toBeInTheDocument();
  });

  it('navigates to play screen and updates the URL', () => {
    render(<App />);
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Play' }));

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
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Play' }));

    window.history.back();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: 'Today' })).toBeInTheDocument();
    });
  });

  it('does not add a history entry for same-path navigation', () => {
    render(<App />);
    const pushState = vi.spyOn(window.history, 'pushState');

    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    fireEvent.click(within(nav).getByRole('button', { name: 'Play' }));
    fireEvent.click(within(nav).getByRole('button', { name: 'Play' }));

    expect(pushState).toHaveBeenCalledTimes(1);
  });

  it('renders an explicit missing-route state', () => {
    window.history.replaceState(null, '', '/missing');

    render(<App />);

    expect(screen.getByTestId('not-found')).toHaveTextContent('Page not found');
  });

  it('navigates to import screen', () => {
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Library' }));
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
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Train' }));
    
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

it('asks once for a display name before creating the active player', async () => {
  localStorage.clear();
  const fetchMock = vi.fn().mockImplementation(async (_url: string, init: RequestInit) => {
    const player = JSON.parse(init.body as string) as { id: string; display_name: string };
    return { ok: true, json: async () => ({ ...player, preferences: {}, access_token: 'named-token' }) };
  });
  vi.stubGlobal('fetch', fetchMock);
  const view = render(<App />);
  expect(screen.getByRole('button', { name: 'Start learning' })).toBeDisabled();
  expect(fetchMock).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: '  Ada  ' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start learning' }));
  await screen.findByRole('heading', { name: 'Today' });
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(JSON.parse(fetchMock.mock.calls[0][1].body as string)).toMatchObject({ display_name: 'Ada' });
  expect(localStorage.getItem('scan64_player_id')).toBeTruthy();
  view.unmount();
  render(<App />);
  expect(screen.queryByLabelText('Display name')).not.toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Play' }));
  expect(screen.getByTestId('player-id-input')).toHaveValue(localStorage.getItem('scan64_player_id'));
  const playerId = localStorage.getItem('scan64_player_id')!;
  const createdPlayer = JSON.parse(fetchMock.mock.calls[0][1].body as string) as { display_name: string };
  vi.mocked(ApiClient.getPlayerProfile).mockResolvedValueOnce({
    player_id: playerId, rating: 1500, display_name: createdPlayer.display_name,
  });
  fireEvent.click(screen.getByRole('button', { name: 'Library' }));
  fireEvent.click(screen.getByRole('button', { name: 'Import PGN' }));
  fireEvent.change(screen.getByTestId('pgn-textarea'), { target: { value: '[Black "Ada"]\n\n1. e4 *' } });
  await waitFor(() => expect(screen.getByLabelText('Your side')).toHaveValue('black'));
  expect(ApiClient.getPlayerProfile).toHaveBeenCalledWith(playerId);
});

it('keeps a failed registration on the name form without storing an identity', async () => {
  localStorage.clear();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, statusText: 'Unavailable' }));
  render(<App />);
  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Ada' } });
  fireEvent.click(screen.getByRole('button', { name: 'Start learning' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('Failed to create player');
  expect(localStorage.getItem('scan64_player_id')).toBeNull();
  expect(screen.getByRole('button', { name: 'Start learning' })).toBeEnabled();
});

it('links the latest analysed game and orders weakest skills by name without codes', async () => {
  const gameId = '00000000-0000-0000-0000-000000000001';
  vi.mocked(ApiClient.getPlayerGames).mockResolvedValue({
    items: [
      { id: '00000000-0000-0000-0000-000000000002', white: 'Newer player', black: 'Bot', result: '*', date: '', created_at: '', diagnosis_count: 0 },
      { id: gameId, white: 'Ada', black: 'Bot', result: '*', date: '', created_at: '', diagnosis_count: 1 },
    ],
    next_cursor: null,
  });
  vi.mocked(ApiClient.getWeeklyReport).mockResolvedValue({
    player_id: 'player-1', games_played: 1, active_concepts_observed: 2,
    active_mastery: [
      { concept_code: 'tactics.pin', name: 'Pin', mastery: 0.7 },
      { concept_code: 'board_awareness.hanging_piece', name: 'Hanging piece', mastery: 0.2 },
    ],
    top_recurring_diagnosis: null,
  });
  render(<App />);
  const skills = await screen.findByRole('heading', { name: 'Skills to practise' });
  expect(within(skills.parentElement!).getAllByRole('listitem').map(item => item.textContent)).toEqual(['Hanging piece', 'Pin']);
  expect(screen.queryByText(/board_awareness|tactics\.pin/)).not.toBeInTheDocument();
  expect(screen.getByRole('group', { name: 'Choose your next step' })).toBeInTheDocument();
  expect(screen.getByText('* · 1 lesson')).toBeInTheDocument();
  expect(screen.getByText('1 game played in the last 7 days.')).toBeInTheDocument();
  expect(screen.queryByText('Newer player vs Bot')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('link', { name: 'Review this game' }));
  expect(window.location.pathname).toBe(`/games/${gameId}/analysis`);
  await waitFor(() => expect(ApiClient.getPositions).toHaveBeenCalledWith(gameId));
});

it('reaches every existing screen through the grouped navigation', async () => {
  render(<App />);
  const nav = screen.getByRole('navigation', { name: 'Main navigation' });
  for (const [label, path] of [
    ['Play', '/play'], ['Review', '/games'], ['Train', '/training'],
    ['Profile', '/profile'], ['Coach', '/coach'], ['Today', '/'],
  ]) {
    fireEvent.click(within(nav).getByRole('button', { name: label }));
    expect(window.location.pathname).toBe(path);
  }
  for (const [label, path] of [
    ['Opening Explorer', '/explorer'], ['Famous Games', '/famous'], ['Import PGN', '/import'],
  ]) {
    fireEvent.click(within(nav).getByRole('button', { name: 'Library' }));
    fireEvent.click(screen.getByRole('button', { name: label }));
    expect(window.location.pathname).toBe(path);
  }
  fireEvent.click(within(nav).getByRole('button', { name: 'Review' }));
  fireEvent.click(screen.getByRole('button', { name: 'Analysis Board' }));
  expect(window.location.pathname).toBe('/analysis');
  expect(screen.getByRole('heading', { name: 'Analysis Board' })).toBeInTheDocument();
  await waitFor(() => expect(ApiClient.getFamousGames).toHaveBeenCalled());
});

it('shows not-found rather than onboarding for an unknown first-visit path', () => {
  localStorage.clear();
  window.history.replaceState(null, '', '/unknown');
  render(<App />);
  expect(screen.getByTestId('not-found')).toBeInTheDocument();
  expect(screen.queryByLabelText('Display name')).not.toBeInTheDocument();
  const today = within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name: 'Today' });
  expect(today).toBeEnabled();
  fireEvent.click(today);
  expect(window.location.pathname).toBe('/');
  expect(screen.getByLabelText('Display name')).toBeInTheDocument();
  expect(screen.queryByTestId('not-found')).not.toBeInTheDocument();
});

it('adopts an identity stored by another tab before name submission', async () => {
  localStorage.clear();
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  render(<App />);
  fireEvent.change(screen.getByLabelText('Display name'), { target: { value: 'Ada' } });
  localStorage.setItem('scan64_player_id', 'other-tab-player');
  localStorage.setItem('scan64_player_token:other-tab-player', 'other-tab-token');
  fireEvent.click(screen.getByRole('button', { name: 'Start learning' }));
  await screen.findByRole('heading', { name: 'Today' });
  await waitFor(() => expect(ApiClient.getPlayerGames).toHaveBeenCalledWith('other-tab-player'));
  expect(localStorage.getItem('scan64_player_id')).toBe('other-tab-player');
  expect(fetchMock).not.toHaveBeenCalled();
});

it('adopts another tab’s stored identity on a storage event and removes its listener on unmount', async () => {
  localStorage.clear();
  const fetchMock = vi.fn();
  vi.stubGlobal('fetch', fetchMock);
  const addListener = vi.spyOn(window, 'addEventListener');
  const removeListener = vi.spyOn(window, 'removeEventListener');
  const view = render(<App />);
  expect(screen.getByLabelText('Display name')).toBeInTheDocument();
  const storageListener = addListener.mock.calls.find(([type]) => type === 'storage')?.[1];
  expect(storageListener).toBeTypeOf('function');
  localStorage.setItem('scan64_player_token:other-tab-player', 'other-tab-token');
  localStorage.setItem('scan64_player_id', 'other-tab-player');
  fireEvent(window, new StorageEvent('storage', {
    key: 'scan64_player_id', newValue: 'other-tab-player', storageArea: localStorage,
  }));
  await screen.findByRole('heading', { name: 'Today' });
  expect(screen.queryByLabelText('Display name')).not.toBeInTheDocument();
  await waitFor(() => expect(ApiClient.getPlayerGames).toHaveBeenCalledWith('other-tab-player'));
  expect(fetchMock).not.toHaveBeenCalled();
  expect(await screen.findByText('No reviewed mistakes to show yet. Analyse a game from Review, or play or import a game.')).toBeInTheDocument();
  view.unmount();
  expect(removeListener).toHaveBeenCalledWith('storage', storageListener);
});
