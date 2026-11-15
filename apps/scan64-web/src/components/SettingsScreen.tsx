import { PuzzleLibraryPanel } from './PuzzleImport';
import { UpdatesPanel } from './Updates';
import { useEffect, useState } from 'react';
import { ListRow, Panel, SegmentedControl, StatusLine } from './Primitives';

export type Appearance = 'system' | 'light' | 'dark';

const APPEARANCE_STORAGE_KEY = 'scan64-appearance';
const APPEARANCE_OPTIONS = [
  { value: 'system', label: 'System' },
  { value: 'light', label: 'Light' },
  { value: 'dark', label: 'Dark' },
] as const;

function readAppearance(): Appearance {
  try {
    const stored = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
  return 'system';
}

function applyAppearance(appearance: Appearance) {
  if (typeof document !== 'undefined') document.documentElement.dataset.appearance = appearance;
}

/** System appearance and native control colour schemes follow the token CSS media query. */
export function initializeAppearance(): Appearance {
  const appearance = readAppearance();
  applyAppearance(appearance);
  return appearance;
}

export function SettingsScreen() {
  const [appearance, setAppearance] = useState<Appearance>(readAppearance);
  const [saveError, setSaveError] = useState(false);

  useEffect(() => {
    applyAppearance(appearance);
  }, [appearance]);

  function changeAppearance(next: Appearance) {
    setAppearance(next);
    try {
      window.localStorage.setItem(APPEARANCE_STORAGE_KEY, next);
      setSaveError(false);
    } catch {
      setSaveError(true);
    }
  }

  return (
    <section className="settings-screen working-screen" aria-labelledby="settings-title">
      <h1 id="settings-title">Settings</h1>
      <Panel title="Appearance">
        <p>Choose a light or dark appearance, or follow your device setting.</p>
        <SegmentedControl
          label="Colour scheme"
          value={appearance}
          options={APPEARANCE_OPTIONS}
          onChange={changeAppearance}
        />
        {saveError ? (
          <StatusLine kind="error">Appearance changed for this session. Your browser did not allow saving the preference.</StatusLine>
        ) : null}
      </Panel>
      <PuzzleLibraryPanel />
      <UpdatesPanel />
      <Panel title="About and licences">
        <p>Scan64 is a local-first chess study app. Play, review your games and practise with lessons grounded in your moves.</p>
        <p>Scan64 is free software licensed under the GNU General Public License, version 3 or later (GPL-3.0-or-later). Stockfish, python-chess and Chessground are also licensed under GPL-3.0-or-later.</p>
        <p><a href="/licenses.html">Read software and artwork licences</a></p>
        <p>Host Grotesk and JetBrains Mono are bundled with the app under the SIL Open Font License 1.1. No font service is contacted.</p>
        <ul className="settings-licences" aria-label="Bundled font licences">
          <ListRow label={<a href="/fonts/host-grotesk-OFL.txt">Host Grotesk licence</a>} />
          <ListRow label={<a href="/fonts/jetbrains-mono-OFL.txt">JetBrains Mono licence</a>} />
        </ul>
      </Panel>
    </section>
  );
}
