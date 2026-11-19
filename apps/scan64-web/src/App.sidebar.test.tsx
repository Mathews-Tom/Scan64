import type { ReactNode } from 'react';
import type * as PuzzleImportModule from './components/PuzzleImport';
import type * as UpdatesModule from './components/Updates';
import type * as PlatformModule from './platform';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import App from './App';
import { isDesktop } from './platform';

vi.mock('./platform', async importOriginal => ({
  ...await importOriginal<typeof PlatformModule>(),
  isDesktop: vi.fn(() => false),
}));

vi.mock('./components/TodayScreen', () => ({ TodayScreen: () => <h1>Today</h1> }));
vi.mock('./components/SettingsScreen', () => ({
  SettingsScreen: () => <section><h1>Settings</h1><section id="puzzle-library" tabIndex={-1}>Puzzle library</section></section>,
}));
vi.mock('./components/PuzzleImport', async importOriginal => ({
  ...await importOriginal<typeof PuzzleImportModule>(),
  PuzzleImportProvider: ({ children }: { children: ReactNode }) => children,
  PuzzleImportFoot: ({ onOpenSettings }: { onOpenSettings?: () => void }) => <button onClick={onOpenSettings}>Open import activity</button>,
}));
vi.mock('./components/Updates', async importOriginal => ({
  ...await importOriginal<typeof UpdatesModule>(),
  UpdatesProvider: ({ children }: { children: ReactNode }) => children,
  UpdatesFoot: () => <div style={{ display: 'none' }}><button>Hidden updater control</button></div>,
}));

const originalWidth = window.innerWidth;
function resize(width: number) {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
  fireEvent(window, new Event('resize'));
}
beforeEach(() => {
  vi.mocked(isDesktop).mockReturnValue(false);
  localStorage.clear();
  localStorage.setItem('scan64_player_id', 'player-1');
  localStorage.setItem('scan64_player_token:player-1', 'test-token');
  window.history.replaceState(null, '', '/');
  resize(1440);
});
afterEach(() => {
  cleanup();
  resize(originalWidth);
});

describe('Sidebar', () => {
  it('defaults wide windows to expanded and restores the saved wide preference after reload', () => {
    const first = render(<App />);
    const collapse = screen.getByRole('button', { name: 'Collapse sidebar' });
    expect(collapse).toHaveAttribute('aria-expanded', 'true');
    expect(collapse).toHaveAttribute('aria-controls', 'main-navigation');
    fireEvent.click(collapse);
    expect(localStorage.getItem('scan64_sidebar_expanded')).toBe('false');
    first.unmount();

    render(<App />);
    const expand = screen.getByRole('button', { name: 'Expand sidebar' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);
    expect(localStorage.getItem('scan64_sidebar_expanded')).toBe('true');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it.each([768, 960, 1199])('opens one modal navigation at %ipx without changing the wide preference', width => {
    resize(width);
    localStorage.setItem('scan64_sidebar_expanded', 'true');
    render(<App />);
    const main = screen.getByRole('main', { name: 'Study content' });
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    const dialog = screen.getByRole('dialog', { name: 'Navigation menu' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(main).toHaveAttribute('inert');
    expect(screen.getAllByRole('navigation', { name: 'Main navigation' })).toHaveLength(1);
    expect(within(dialog).getByRole('button', { name: 'Collapse sidebar' })).toHaveFocus();
    expect(localStorage.getItem('scan64_sidebar_expanded')).toBe('true');
  });

  it('wraps keyboard focus and keeps programmatic focus inside the drawer', () => {
    resize(960);
    render(<App />);
    const main = screen.getByRole('main');
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    const collapse = screen.getByRole('button', { name: 'Collapse sidebar' });
    const last = screen.getByRole('button', { name: 'Open import activity' });
    fireEvent.keyDown(collapse, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(last, { key: 'Tab' });
    expect(collapse).toHaveFocus();
    main.focus();
    expect(collapse).toHaveFocus();
  });

  it.each(['collapse', 'escape', 'backdrop'])('dismisses through %s and returns focus to the toggle', method => {
    resize(960);
    const { container } = render(<App />);
    const main = screen.getByRole('main');
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    if (method === 'collapse') fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    else if (method === 'escape') fireEvent.keyDown(document, { key: 'Escape' });
    else fireEvent.click(container.querySelector('.sidebar-backdrop')!);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(main).not.toHaveAttribute('inert');
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveFocus();
    expect(document.documentElement.style.overflow).not.toBe('hidden');
  });

  it.each(['Library', 'Today'])('dismisses on %s selection and focuses the page, including the current page', async name => {
    resize(960);
    render(<App />);
    await screen.findByRole('heading', { name: 'Today' });
    const main = screen.getByRole('main');
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Main navigation' })).getByRole('button', { name }));
    expect(window.location.pathname).toBe(name === 'Library' ? '/library' : '/');
    expect(await screen.findByRole('heading', { name })).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(main).toHaveFocus();
  });

  it('dismisses on breakpoint changes, restores the wide preference and never persists the temporary drawer', () => {
    resize(960);
    localStorage.setItem('scan64_sidebar_expanded', 'false');
    render(<App />);
    const main = screen.getByRole('main');
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    resize(1200);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toHaveFocus();
    expect(main).not.toHaveAttribute('inert');
    expect(localStorage.getItem('scan64_sidebar_expanded')).toBe('false');
    resize(1199);
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    resize(767);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(main).toHaveFocus();
    expect(main).not.toHaveAttribute('inert');
  });

  it('opens the existing Settings import panel without reloading the document', async () => {
    resize(960);
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    fireEvent.click(screen.getByRole('button', { name: 'Open import activity' }));
    expect(window.location.pathname).toBe('/settings');
    await screen.findByRole('heading', { name: 'Settings' });
    expect(document.getElementById('puzzle-library')).toHaveFocus();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
  it('keeps native navigation expandable on the left at phone-sized effective widths', async () => {
    vi.mocked(isDesktop).mockReturnValue(true);
    resize(390);
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Expand sidebar' }));
    expect(screen.getByRole('dialog', { name: 'Navigation menu' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveFocus();
  });
});
