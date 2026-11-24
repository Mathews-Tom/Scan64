import type { ComponentType, ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import App from './App';

const routes = vi.hoisted(() => ({
  today: Promise.withResolvers<{ TodayScreen: ComponentType }>(),
  play: Promise.withResolvers<{ PlayScreen: ComponentType }>(),
  settings: Promise.withResolvers<{ SettingsScreen: ComponentType }>(),
  packs: Promise.withResolvers<{ LessonPacksPanel: ComponentType }>(),
}));

vi.mock('./components/TodayScreen', () => routes.today.promise);
vi.mock('./components/PlayScreen', () => routes.play.promise);
vi.mock('./components/SettingsScreen', () => routes.settings.promise);
vi.mock('./components/LessonPacksPanel', () => routes.packs.promise);
vi.mock('./components/GamesListScreen', () => ({
  GamesListScreen: () => <h1>Your games</h1>,
}));
vi.mock('./components/PuzzleImport', () => ({
  PuzzleImportProvider: ({ children }: { children: ReactNode }) => children,
  PuzzleImportFoot: ({ onOpenSettings }: { onOpenSettings: () => void }) => (
    <button onClick={onOpenSettings}>Open import activity</button>
  ),
}));
vi.mock('./components/Updates', () => ({
  UpdatesProvider: ({ children }: { children: ReactNode }) => children,
  UpdatesFoot: () => null,
}));

const originalWidth = window.innerWidth;

beforeEach(() => {
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'test-token');
  window.history.replaceState(null, '', '/');
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
});

function navigate(name: string) {
  fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' }))
    .getByRole('button', { name }));
}

describe('lazy route loading', () => {
  it('announces a pending route, permits navigation away, and loads it on return without losing identity', async () => {
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading screen');
    expect(screen.queryByRole('heading', { name: 'Today' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Display name')).not.toBeInTheDocument();

    navigate('Library');
    expect(screen.getByRole('heading', { name: 'Library' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading lesson packs');
    expect(screen.getByRole('main', { name: 'Study content' })).toHaveFocus();
    await act(async () => {
      routes.today.resolve({ TodayScreen: () => <h1>Today</h1> });
      await routes.today.promise;
    });
    expect(window.location.pathname).toBe('/library');
    expect(screen.getByRole('heading', { name: 'Library' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Today' })).not.toBeInTheDocument();

    navigate('Today');
    expect(await screen.findByRole('heading', { name: 'Today' })).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Display name')).not.toBeInTheDocument();
    expect(localStorage.getItem('scan64_player_id')).toBe('player-1');
    expect(localStorage.getItem('scan64_player_token:player-1')).toBe('test-token');
    expect(document.title).toBe('Today · Scan64');
  });

  it('focuses the requested Settings panel on deferred and cached visits', async () => {
    window.history.replaceState(null, '', '/library');
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 960 });
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open import activity' }));

    expect(window.location.pathname).toBe('/settings');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Loading screen');
    expect(screen.getByRole('main')).toHaveFocus();
    expect(screen.queryByRole('region', { name: 'Puzzle library' })).not.toBeInTheDocument();
    await act(async () => {
      routes.settings.resolve({
        SettingsScreen: () => <section>
          <h1>Settings</h1>
          <section id="puzzle-library" aria-label="Puzzle library" tabIndex={-1}>Puzzle library</section>
        </section>,
      });
      await routes.settings.promise;
    });

    expect(await screen.findByRole('region', { name: 'Puzzle library' })).toHaveFocus();
    expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(document.title).toBe('Settings · Scan64');

    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    navigate('Library');
    expect(screen.getByRole('main')).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open import activity' }));
    expect(screen.getByRole('region', { name: 'Puzzle library' })).toHaveFocus();
  });

  it('shows a rejected route error while keeping navigation usable and recovers on another route', async () => {
    window.history.replaceState(null, '', '/play');
    vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<App />);
    expect(screen.getByRole('status')).toHaveTextContent('Loading screen');

    await act(async () => {
      routes.play.reject(new Error('Route chunk download failed'));
      await routes.play.promise.catch(() => {});
    });
    expect(await screen.findByRole('alert')).toBeVisible();
    expect(screen.getByRole('heading', { name: 'Screen unavailable' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload screen' })).toBeEnabled();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();

    navigate('Review');
    expect(await screen.findByRole('heading', { name: 'Your games' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/games');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reload screen' })).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveFocus();
    expect(document.title).toBe('Your games · Scan64');
  });
});
