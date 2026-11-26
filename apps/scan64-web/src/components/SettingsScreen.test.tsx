import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsScreen } from './SettingsScreen';
import { initializeAppearance } from '../appearance';
import { Button, Dialog, EmptyState, Field, ListRow, Panel, ProgressBar, SegmentedControl, StatusLine } from './Primitives';
import { BOARD_THEME_OPTIONS, initializeBoardAppearance, PIECE_SET_OPTIONS } from '../boardAppearance';
import { saveBoardSounds } from '../boardSounds';

beforeEach(() => {
  localStorage.clear();
  saveBoardSounds(true);
  delete document.documentElement.dataset.appearance;
  delete document.documentElement.dataset.pieceSet;
  delete document.documentElement.dataset.boardTheme;
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  delete document.documentElement.dataset.appearance;
  delete document.documentElement.dataset.pieceSet;
  delete document.documentElement.dataset.boardTheme;
});

describe('Settings', () => {
  it('defaults move sounds on, persists mute and restores it when reopened', () => {
    const view = render(<SettingsScreen />);
    const toggle = screen.getByRole('checkbox', { name: 'Move sounds' });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(toggle).not.toBeChecked();
    expect(localStorage.getItem('scan64-board-sounds')).toBe('false');
    view.unmount();
    render(<SettingsScreen />);
    expect(screen.getByRole('checkbox', { name: 'Move sounds' })).not.toBeChecked();
  });

  it('keeps a session mute and explains when storage cannot save it', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Move sounds' }));
    expect(screen.getByRole('checkbox', { name: 'Move sounds' })).not.toBeChecked();
    expect(screen.getByRole('alert')).toHaveTextContent('Sounds changed for this session');
  });

  it('offers named appearance controls and bundled licences without a player identity', () => {
    render(<SettingsScreen />);
    expect(screen.getByRole('heading', { level: 1, name: 'Settings' })).toBeInTheDocument();
    const appearance = screen.getByRole('group', { name: 'Colour scheme' });
    expect(within(appearance).getByRole('radio', { name: 'System' })).toBeChecked();
    expect(within(appearance).getByRole('radio', { name: 'Light' })).not.toBeChecked();
    expect(within(appearance).getByRole('radio', { name: 'Dark' })).not.toBeChecked();
    expect(screen.getByRole('heading', { name: 'About and licences' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Host Grotesk licence' })).toHaveAttribute('href', '/fonts/host-grotesk-OFL.txt');
    expect(screen.getByRole('link', { name: 'JetBrains Mono licence' })).toHaveAttribute('href', '/fonts/jetbrains-mono-OFL.txt');
  });

  it.each(['system', 'light', 'dark'] as const)('initializes the saved %s appearance before a screen opens', (appearance) => {
    localStorage.setItem('scan64-appearance', appearance);
    expect(initializeAppearance()).toBe(appearance);
    expect(document.documentElement.dataset.appearance).toBe(appearance);
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: new RegExp(`^${appearance}$`, 'i') })).toBeChecked();
  });

  it('applies and persists each choice and restores the selection when reopened', () => {
    const first = render(<SettingsScreen />);
    for (const [name, value] of [['Dark', 'dark'], ['Light', 'light'], ['System', 'system']]) {
      fireEvent.click(screen.getByRole('radio', { name }));
      expect(screen.getByRole('radio', { name })).toBeChecked();
      expect(localStorage.getItem('scan64-appearance')).toBe(value);
      expect(document.documentElement.dataset.appearance).toBe(value);
    }
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    first.unmount();
    initializeAppearance();
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
  });

  it('uses System for an invalid saved value without requiring matchMedia', () => {
    localStorage.setItem('scan64-appearance', 'invalid');
    vi.stubGlobal('matchMedia', undefined);
    expect(initializeAppearance()).toBe('system');
    expect(document.documentElement.dataset.appearance).toBe('system');
  });

  it('still initializes and changes appearance when storage is unavailable', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    vi.stubGlobal('matchMedia', undefined);
    expect(initializeAppearance()).toBe('system');
    render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(screen.getByRole('radio', { name: 'Dark' })).toBeChecked();
    expect(document.documentElement.dataset.appearance).toBe('dark');
    expect(screen.getByRole('alert')).toHaveTextContent(/did not allow saving/i);
  });
});

const lastPieceSet = PIECE_SET_OPTIONS[PIECE_SET_OPTIONS.length - 1];
const lastBoardTheme = BOARD_THEME_OPTIONS[BOARD_THEME_OPTIONS.length - 1];
const MATERIAL_BOARDS = BOARD_THEME_OPTIONS.filter((option) => option.material !== 'flat');

describe('Staunton piece sets', () => {
  it('groups Staunton-pattern sets apart from other designs and defaults to Staunton (cburnett)', () => {
    initializeBoardAppearance();
    render(<SettingsScreen />);
    const pieceSets = screen.getByRole('group', { name: 'Piece set' });
    const staunton = within(pieceSets).getByRole('group', { name: 'Staunton pattern' });
    const other = within(pieceSets).getByRole('group', { name: 'Other designs' });
    expect(within(staunton).getAllByRole('radio').map((radio) => (radio as HTMLInputElement).value)).toEqual([
      'cburnett', 'merida', 'chessnut', 'california', 'maestro', 'staunty', 'mpchess', 'sophia',
    ]);
    expect(within(other).getAllByRole('radio').map((radio) => (radio as HTMLInputElement).value)).toEqual(['spatial', 'firi']);
    expect(within(staunton).getByRole('radio', { name: 'Staunton (cburnett)' })).toBeChecked();
    expect(document.documentElement.dataset.pieceSet).toBe('cburnett');
    const names = new Set(within(pieceSets).getAllByRole('radio').map((radio) => (radio as HTMLInputElement).name));
    expect(names.size).toBe(1);
  });

  it('switches between groups with one selection', () => {
    initializeBoardAppearance();
    render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('radio', { name: 'Spatial' }));
    fireEvent.click(screen.getByRole('radio', { name: 'mpchess' }));
    const pieceSets = screen.getByRole('group', { name: 'Piece set' });
    expect(within(pieceSets).getAllByRole('radio', { checked: true })).toEqual([screen.getByRole('radio', { name: 'mpchess' })]);
    expect(localStorage.getItem('scan64-piece-set')).toBe('mpchess');
  });

  it.each(PIECE_SET_OPTIONS.map(({ value, label }) => [value, label] as const))('keeps a saved %s choice at startup', (value, label) => {
    localStorage.setItem('scan64-piece-set', value);
    expect(initializeBoardAppearance().pieceSet).toBe(value);
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: label })).toBeChecked();
  });
});

describe('Material boards', () => {
  it('groups flat palettes, wood and stone boards and keeps Brown and cream as the default', () => {
    initializeBoardAppearance();
    render(<SettingsScreen />);
    const boards = screen.getByRole('group', { name: 'Board palette' });
    const groups = ['Flat colours', 'Wood', 'Stone'] as const;
    const radios = Object.fromEntries(groups.map((name) => [name, within(within(boards).getByRole('group', { name })).getAllByRole('radio').map((radio) => radio.closest('label')?.textContent)]));
    expect(radios['Flat colours']).toEqual(['Brown and cream', 'Green and buff', 'Classic brown', 'Classic green', 'Walnut', 'Sand', 'Slate', 'Sage', 'Graphite']);
    expect(radios.Wood).toEqual(['Walnut and maple', 'Rosewood and boxwood', 'Mahogany and maple', 'Ebony and boxwood']);
    expect(radios.Stone).toEqual(['White and black marble', 'Grey and blue granite']);
    expect(within(boards).getByRole('radio', { name: 'Brown and cream' })).toBeChecked();
    expect(within(boards).getAllByRole('radio').filter((radio) => (radio as HTMLInputElement).checked)).toHaveLength(1);
    expect(document.documentElement.dataset.boardTheme).toBe('brown');
  });

  it('moves one selection across the flat, wood and stone groups', () => {
    render(<SettingsScreen />);
    for (const label of ['Ebony and boxwood', 'Grey and blue granite', 'Sage', 'Walnut and maple']) {
      fireEvent.click(screen.getByRole('radio', { name: label }));
      const checked = within(screen.getByRole('group', { name: 'Board palette' })).getAllByRole('radio').filter((radio) => (radio as HTMLInputElement).checked);
      expect(checked.map((radio) => radio.closest('label')?.textContent)).toEqual([label]);
    }
    expect(localStorage.getItem('scan64-board-theme')).toBe('walnut-maple');
  });

  it.each(MATERIAL_BOARDS.map(({ value, label }) => [value, label] as const))('saves %s and restores it at startup', (value, label) => {
    const view = render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('radio', { name: label }));
    expect(localStorage.getItem('scan64-board-theme')).toBe(value);
    expect(document.documentElement.dataset.boardTheme).toBe(value);
    view.unmount();
    delete document.documentElement.dataset.boardTheme;
    expect(initializeBoardAppearance()).toEqual({ pieceSet: 'cburnett', boardTheme: value });
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: label })).toBeChecked();
  });

  it('keeps earlier saved palettes unchanged', () => {
    for (const { value, label } of BOARD_THEME_OPTIONS.filter((option) => option.material === 'flat')) {
      localStorage.setItem('scan64-board-theme', value);
      expect(initializeBoardAppearance().boardTheme).toBe(value);
      const view = render(<SettingsScreen />);
      expect(screen.getByRole('radio', { name: label })).toBeChecked();
      view.unmount();
    }
  });
});

describe('Independent board preferences', () => {
  it('persists every piece set and palette without changing the app appearance', () => {
    localStorage.setItem('scan64-appearance', 'dark');
    initializeAppearance();
    const view = render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('radio', { name: 'Firi' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Green and buff' }));
    for (const { value: pieceSet, label } of PIECE_SET_OPTIONS) {
      fireEvent.click(screen.getByRole('radio', { name: label }));
      expect(localStorage.getItem('scan64-piece-set')).toBe(pieceSet);
      expect(document.documentElement.dataset.pieceSet).toBe(pieceSet);
      expect(document.documentElement.dataset.boardTheme).toBe('green');
      expect(document.documentElement.dataset.appearance).toBe('dark');
    }
    for (const { value: boardTheme, label: boardLabel } of BOARD_THEME_OPTIONS) {
      fireEvent.click(screen.getByRole('radio', { name: boardLabel }));
      expect(localStorage.getItem('scan64-board-theme')).toBe(boardTheme);
      expect(document.documentElement.dataset.boardTheme).toBe(boardTheme);
      expect(document.documentElement.dataset.pieceSet).toBe(lastPieceSet.value);
      expect(document.documentElement.dataset.appearance).toBe('dark');
    }
    fireEvent.click(screen.getByRole('radio', { name: 'Light' }));
    expect(document.documentElement.dataset.pieceSet).toBe(lastPieceSet.value);
    expect(document.documentElement.dataset.boardTheme).toBe(lastBoardTheme.value);
    view.unmount();
    expect(initializeBoardAppearance()).toEqual({ pieceSet: lastPieceSet.value, boardTheme: lastBoardTheme.value });
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: lastPieceSet.label })).toBeChecked();
    expect(screen.getByRole('radio', { name: lastBoardTheme.label })).toBeChecked();
    expect(screen.getByRole('radio', { name: 'Light' })).toBeChecked();
  });

  it('validates each saved choice independently at startup', () => {
    localStorage.setItem('scan64-piece-set', 'untrusted');
    localStorage.setItem('scan64-board-theme', 'walnut');
    expect(initializeBoardAppearance()).toEqual({ pieceSet: 'cburnett', boardTheme: 'walnut' });
    expect(document.documentElement.dataset.pieceSet).toBe('cburnett');
    expect(document.documentElement.dataset.boardTheme).toBe('walnut');
    localStorage.setItem('scan64-piece-set', 'firi');
    localStorage.setItem('scan64-board-theme', 'untrusted');
    expect(initializeBoardAppearance()).toEqual({ pieceSet: 'firi', boardTheme: 'brown' });
    for (const kept of ['slate', 'sage', 'graphite'] as const) {
      localStorage.setItem('scan64-board-theme', kept);
      expect(initializeBoardAppearance()).toEqual({ pieceSet: 'firi', boardTheme: kept });
    }
  });

  it.each([
    ['piece set', 'Firi', 'firi', 'pieceSet', 'scan64-piece-set', 'spatial', 'Spatial'],
    ['piece set', 'Firi', 'firi', 'pieceSet', 'scan64-piece-set', null, 'Staunton (cburnett)'],
    ['board palette', 'Green and buff', 'green', 'boardTheme', 'scan64-board-theme', 'walnut', 'Walnut'],
    ['board palette', 'Green and buff', 'green', 'boardTheme', 'scan64-board-theme', null, 'Brown and cream'],
  ] as const)('keeps %s %s across remounts when saving fails (case %#)', (preference, choice, value, datasetKey, storageKey, savedValue, savedChoice) => {
    if (savedValue !== null) localStorage.setItem(storageKey, savedValue);
    initializeBoardAppearance();
    const first = render(<SettingsScreen />);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });

    fireEvent.click(screen.getByRole('radio', { name: choice }));
    expect(screen.getByRole('alert')).toHaveTextContent(new RegExp(`${preference} changed for this session`, 'i'));
    expect(document.documentElement.dataset[datasetKey]).toBe(value);
    expect(localStorage.getItem(storageKey)).toBe(savedValue);

    first.unmount();
    const reopened = render(<SettingsScreen />);
    expect(document.documentElement.dataset[datasetKey]).toBe(value);
    expect(screen.getByRole('radio', { name: choice })).toBeChecked();
    expect(screen.getByRole('radio', { name: savedChoice })).not.toBeChecked();

    reopened.unmount();
    // Startup must read saved/default values instead of the outgoing session.
    initializeBoardAppearance();
    render(<SettingsScreen />);
    expect(screen.getByRole('radio', { name: savedChoice })).toBeChecked();
    expect(document.documentElement.dataset[datasetKey]).toBe(savedValue ?? (datasetKey === 'pieceSet' ? 'cburnett' : 'brown'));
  });

  it('keeps session choices and separate error states when persistence is blocked', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    const save = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Storage blocked'); });
    expect(initializeBoardAppearance()).toEqual({ pieceSet: 'cburnett', boardTheme: 'brown' });
    render(<SettingsScreen />);
    fireEvent.click(screen.getByRole('radio', { name: 'Firi' }));
    fireEvent.click(screen.getByRole('radio', { name: 'Green and buff' }));
    expect(document.documentElement.dataset.pieceSet).toBe('firi');
    expect(document.documentElement.dataset.boardTheme).toBe('green');
    expect(screen.getAllByRole('alert')).toHaveLength(2);
    save.mockImplementation(() => undefined);
    fireEvent.click(screen.getByRole('radio', { name: 'Spatial' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Board palette changed for this session');
    fireEvent.click(screen.getByRole('radio', { name: 'Classic green' }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });
});

describe('Identity primitives', () => {
  it('provides named primary and secondary buttons without implicit form submission', () => {
    const onClick = vi.fn();
    render(<><Button variant="primary" onClick={onClick}>Save changes</Button><Button>Cancel changes</Button></>);
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    expect(onClick).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Cancel changes' })).toHaveAttribute('type', 'button');
  });

  it('names panels and associates field labels, hints and errors', () => {
    render(<Panel title="Player"><Field label="Display name" hint="Use a name you recognise." error="Enter a display name." /></Panel>);
    const panel = screen.getByRole('region', { name: 'Player' });
    const input = within(panel).getByRole('textbox', { name: 'Display name' });
    expect(input).toHaveAccessibleDescription('Use a name you recognise. Enter a display name.');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(within(panel).getByRole('alert')).toHaveTextContent('Enter a display name.');
  });

  it('uses native radio selection and honours disabled options', () => {
    function Choice() {
      const [value, setValue] = useState('first');
      return <SegmentedControl label="Study mode" value={value} onChange={setValue} options={[
        { value: 'first', label: 'Guided' },
        { value: 'second', label: 'Independent' },
        { value: 'third', label: 'Unavailable', disabled: true },
      ]} />;
    }
    render(<Choice />);
    const group = screen.getByRole('group', { name: 'Study mode' });
    fireEvent.click(within(group).getByRole('radio', { name: 'Independent' }));
    expect(within(group).getByRole('radio', { name: 'Independent' })).toBeChecked();
    expect(within(group).getByRole('radio', { name: 'Guided' })).not.toBeChecked();
    expect(within(group).getByRole('radio', { name: 'Unavailable' })).toBeDisabled();
  });

  it('names progress and distinguishes known progress from an unknown total', () => {
    const view = render(<ProgressBar label="Import puzzles" value={25} />);
    expect(screen.getByRole('progressbar', { name: 'Import puzzles' })).toHaveAttribute('value', '25');
    expect(screen.getByRole('progressbar', { name: 'Import puzzles' })).toHaveAttribute('aria-valuetext', '25%');
    view.rerender(<ProgressBar label="Download puzzles" />);
    expect(screen.getByRole('progressbar', { name: 'Download puzzles' })).not.toHaveAttribute('value');
    expect(screen.getByRole('progressbar', { name: 'Download puzzles' })).toHaveAttribute('aria-valuetext', 'In progress');
  });

  it('announces status changes and errors with words, not colour alone', () => {
    const view = render(<StatusLine>Import complete</StatusLine>);
    expect(screen.getByRole('status')).toHaveTextContent('Import complete');
    view.rerender(<StatusLine kind="error">Import failed</StatusLine>);
    expect(screen.getByRole('alert')).toHaveTextContent('Error: Import failed');
  });

  it('renders semantic list rows and actionable empty states', () => {
    render(<><ul aria-label="Games"><ListRow label="First game" description="Ready to review" trailing={<Button>Review game</Button>} /></ul><EmptyState title="No lessons yet" action={<Button>Play a game</Button>}><p>Play a game to find a position to practise.</p></EmptyState></>);
    expect(within(screen.getByRole('list', { name: 'Games' })).getAllByRole('listitem')).toHaveLength(1);
    expect(screen.getByRole('button', { name: 'Review game' })).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'No lessons yet' })).getByRole('button', { name: 'Play a game' })).toBeInTheDocument();
  });

  it('opens a named native modal and supports Escape and its close control', () => {
    const prototype = HTMLDialogElement.prototype;
    const showModalDescriptor = Object.getOwnPropertyDescriptor(prototype, 'showModal');
    const closeDescriptor = Object.getOwnPropertyDescriptor(prototype, 'close');
    const showModal = vi.fn(function (this: HTMLDialogElement) { this.open = true; });
    const close = vi.fn(function (this: HTMLDialogElement) { this.open = false; });
    Object.defineProperty(prototype, 'showModal', { configurable: true, value: showModal });
    Object.defineProperty(prototype, 'close', { configurable: true, value: close });
    function Details() {
      const [open, setOpen] = useState(true);
      return <><Button onClick={() => setOpen(true)}>Open details</Button><Dialog open={open} onClose={() => setOpen(false)} title="Game details" description="Review this game before continuing."><p>Your game is saved locally.</p></Dialog></>;
    }
    try {
      render(<Details />);
      const dialog = screen.getByRole('dialog', { name: 'Game details' });
      expect(dialog).toHaveAccessibleDescription('Review this game before continuing.');
      expect(showModal).toHaveBeenCalledOnce();
      fireEvent(dialog, new Event('cancel', { cancelable: true }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      expect(close).toHaveBeenCalledOnce();
      fireEvent.click(screen.getByRole('button', { name: 'Open details' }));
      fireEvent.click(screen.getByRole('button', { name: 'Close dialog' }));
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    } finally {
      cleanup();
      if (showModalDescriptor) Object.defineProperty(prototype, 'showModal', showModalDescriptor);
      else Reflect.deleteProperty(prototype, 'showModal');
      if (closeDescriptor) Object.defineProperty(prototype, 'close', closeDescriptor);
      else Reflect.deleteProperty(prototype, 'close');
    }
  });
});
