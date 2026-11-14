import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { apiBase, apiFetch, choosePuzzleFile, isDesktop } from '../platform';
import { Button, Panel, ProgressBar, StatusLine } from './Primitives';

export interface PuzzleImportState {
  status: 'idle' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed';
  phase: 'download' | 'read' | 'import' | 'save' | null;
  done: number;
  total: number | null;
  unit: 'bytes' | 'puzzles';
  bytes_per_second: number | null;
  error: string | null;
  summary: { accepted: number; inserted: number; existing: number; read: number } | null;
}
const idle: PuzzleImportState = { status: 'idle', phase: null, done: 0, total: null, unit: 'puzzles', bytes_per_second: null, error: null, summary: null };
const activeJob = (state: PuzzleImportState) => state.status === 'running' || state.status === 'cancelling';
interface Controller {
  state: PuzzleImportState;
  desktop: boolean;
  pending: boolean;
  error: string | null;
  download: () => void;
  chooseFile: () => void;
  cancel: () => void;
  startPuzzles: () => void;
}
const ImportContext = createContext<Controller | null>(null);

export function PuzzleImportProvider({ children, onStartPuzzles }: { children: ReactNode; onStartPuzzles: () => void }) {
  const desktop = isDesktop();
  const [state, setState] = useState(idle);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const busy = useRef(false);
  const version = useRef(0);
  const mounted = useRef(true);
  const request = useCallback(async (method: 'GET' | 'POST' | 'DELETE', body?: object) => {
    const response = await apiFetch(`${apiBase()}/app/puzzle-import`, {
      method,
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
    });
    if (!response.ok) {
      const detail: unknown = await response.json().catch(() => null);
      throw new Error(detail && typeof detail === 'object' && 'detail' in detail && typeof detail.detail === 'string' ? detail.detail : `Puzzle import request failed (${response.status}).`);
    }
    return await response.json() as PuzzleImportState;
  }, []);
  const running = activeJob(state);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; version.current += 1; };
  }, []);
  useEffect(() => {
    if (!desktop) return;
    let stopped = false;
    let timer: number | undefined;
    const poll = async () => {
      const revision = version.current;
      try {
        const next = await request('GET');
        if (!stopped && !busy.current && revision === version.current) { setState(next); setError(null); }
      } catch (failure) {
        if (!stopped && !busy.current && revision === version.current) setError(failure instanceof Error ? failure.message : 'Could not read puzzle import progress.');
      } finally {
        if (!stopped && running) timer = window.setTimeout(() => void poll(), 500);
      }
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [desktop, running, request]);
  const act = async (action: 'download' | 'file' | 'cancel') => {
    if (!desktop || busy.current || (action !== 'cancel' && running)) return;
    busy.current = true; setPending(true); setError(null);
    version.current += 1;
    try {
      const path = action === 'file' ? await choosePuzzleFile() : null;
      if (!mounted.current || (action === 'file' && path === null)) return;
      const next = await request(action === 'cancel' ? 'DELETE' : 'POST', action === 'cancel' ? undefined : action === 'file' ? { source: 'file', path } : { source: 'download' });
      version.current += 1;
      if (mounted.current) setState(next);
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : 'Puzzle import failed. Try again.');
    } finally {
      busy.current = false;
      if (mounted.current) setPending(false);
    }
  };
  return <ImportContext.Provider value={{ state, desktop, pending, error, download: () => void act('download'), chooseFile: () => void act('file'), cancel: () => void act('cancel'), startPuzzles: onStartPuzzles }}>{children}</ImportContext.Provider>;
}

export function usePuzzleImport() { return useContext(ImportContext); }
const phaseNames = { download: 'Download', read: 'Read', import: 'Import', save: 'Save' };
export function importProgressText(state: PuzzleImportState): string {
  const amount = (value: number) => state.unit === 'bytes' ? `${(value / 1_000_000).toFixed(1)} MB` : `${value.toLocaleString()} puzzles`;
  const progress = `${amount(state.done)}${state.total !== null ? ` of ${amount(state.total)}` : ''}`;
  const speed = state.bytes_per_second;
  const timing = state.unit === 'bytes' && speed !== null && speed > 0
    ? ` · ${(speed / 1_000_000).toFixed(1)} MB/s${state.total !== null ? ` · ${Math.ceil(Math.max(0, state.total - state.done) / speed)} seconds left` : ''}` : '';
  return `${state.status === 'cancelling' ? 'Cancelling' : state.phase ? phaseNames[state.phase] : 'Preparing'} · ${progress}${timing}`;
}
export function PuzzleImportFoot() {
  const controller = usePuzzleImport();
  return controller?.desktop && activeJob(controller.state)
    ? <p className="puzzle-import-foot">{importProgressText(controller.state)}</p> : null;
}
export function PuzzleLibraryControls() {
  const controller = usePuzzleImport();
  if (!controller?.desktop) return <><p>Ask the operator to import puzzles into the application database:</p><code>uv run scan64-cli puzzles import /path/to/lichess_db_puzzle.csv.zst --json</code></>;
  const { state, pending, error } = controller;
  const active = activeJob(state);
  return <div>
    <p>Download the compressed puzzle database from database.lichess.org (about 270 MB; the size changes as the database grows). Keep 50,000 puzzles rated 600–1800. Nothing downloads automatically.</p>
    {active ? <>
      <p>Download → Read → Import → Save</p>
      <ProgressBar label={state.phase ? phaseNames[state.phase] : 'Preparing puzzle import'} value={state.total !== null ? state.done : undefined} max={state.total ?? undefined} valueText={importProgressText(state)} />
      <StatusLine>{state.status === 'cancelling' ? 'Cancelling puzzle import…' : `${state.phase ? phaseNames[state.phase] : 'Preparing'} in progress. You can leave this page.`}</StatusLine>
      <Button disabled={pending || state.status === 'cancelling'} onClick={controller.cancel}>{state.phase === 'download' ? 'Cancel download' : 'Cancel import'}</Button>
    </> : <>
      {state.status === 'succeeded' ? <>
        <StatusLine>Puzzle library ready. {state.summary?.inserted.toLocaleString() ?? 0} added; {state.summary?.existing.toLocaleString() ?? 0} already in your library.</StatusLine>
        <Button variant="primary" onClick={controller.startPuzzles}>Start puzzles</Button>
      </> : null}
      {state.status === 'cancelled' ? <StatusLine>Import cancelled. Your puzzle library is unchanged.</StatusLine> : null}
      {state.status === 'failed' ? <StatusLine kind="error">{state.error ?? 'Import failed. Your puzzle library is unchanged. Try again.'}</StatusLine> : null}
      <Button variant={state.status === 'succeeded' ? 'secondary' : 'primary'} disabled={pending} onClick={controller.download}>Download puzzles</Button>
      <Button disabled={pending} onClick={controller.chooseFile}>Choose a file…</Button>
    </>}
    {error ? <StatusLine kind="error">{error}</StatusLine> : null}
  </div>;
}
export function PuzzleLibraryPanel() { return <Panel title="Puzzle library"><PuzzleLibraryControls /></Panel>; }
