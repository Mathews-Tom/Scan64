import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { apiBase, apiFetch, choosePuzzleFile, isDesktop } from '../platform';
import { readProgress } from '../api/progress';
import { Button, Panel, ProgressBar, StatusLine } from './Primitives';

export interface PuzzleImportState {
  revision: number;
  status: 'idle' | 'running' | 'cancelling' | 'cancelled' | 'succeeded' | 'failed';
  phase: 'download' | 'read' | 'import' | 'save' | null;
  done: number;
  total: number | null;
  unit: 'bytes' | 'puzzles';
  bytes_per_second: number | null;
  error: string | null;
  summary: { accepted: number; inserted: number; existing: number; read: number } | null;
}
const idle: PuzzleImportState = { revision: 0, status: 'idle', phase: null, done: 0, total: null, unit: 'puzzles', bytes_per_second: null, error: null, summary: null };
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
  const latest = useRef(idle);
  const revision = useRef(-1);
  const stream = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const accept = useCallback((next: PuzzleImportState) => {
    if (!Number.isSafeInteger(next.revision) || next.revision < 0 || !['idle', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed'].includes(next.status)) {
      throw new Error('Malformed puzzle import progress.');
    }
    if (!mounted.current || next.revision <= revision.current) return;
    revision.current = next.revision;
    latest.current = next;
    setState(next);
    setError(null);
  }, []);
  const request = useCallback(async (method: 'POST' | 'DELETE', body?: object) => {
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
  const connect = useCallback(async () => {
    stream.current?.abort();
    const controller = new AbortController();
    stream.current = controller;
    try {
      const response = await apiFetch(`${apiBase()}/app/puzzle-import/events`, { signal: controller.signal });
      await readProgress<PuzzleImportState>(response, controller.signal, next => {
        if (stream.current === controller) accept(next);
      }, next => !activeJob(next));
    } catch (failure) {
      if (!controller.signal.aborted && mounted.current && stream.current === controller) {
        setError(failure instanceof Error ? failure.message : 'Could not read puzzle import progress.');
      }
    } finally {
      if (stream.current === controller) stream.current = null;
    }
  }, [accept]);
  const running = activeJob(state);
  useEffect(() => {
    mounted.current = true;
    if (desktop) void connect();
    return () => {
      mounted.current = false;
      stream.current?.abort();
      stream.current = null;
    };
  }, [desktop, connect]);
  const act = async (action: 'download' | 'file' | 'cancel') => {
    if (!desktop || busy.current || (action !== 'cancel' && running) || (action === 'cancel' && state.status === 'cancelling')) return;
    busy.current = true; setPending(true); setError(null);
    try {
      const path = action === 'file' ? await choosePuzzleFile() : null;
      if (!mounted.current || (action === 'file' && path === null)) return;
      const next = await request(action === 'cancel' ? 'DELETE' : 'POST', action === 'cancel' ? undefined : action === 'file' ? { source: 'file', path } : { source: 'download' });
      if (mounted.current) {
        accept(next);
        if (activeJob(latest.current) && (action !== 'cancel' || stream.current === null)) void connect();
      }
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error ? failure.message : typeof failure === 'string' ? failure : 'Puzzle import failed. Try again.');
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
  if (state.phase === 'save') return state.status === 'cancelling' ? 'Cancelling puzzle import…' : 'Saving puzzle catalog…';
  const amount = (value: number) => state.unit === 'bytes' ? `${(value / 1_000_000).toFixed(1)} MB` : `${value.toLocaleString()} puzzles`;
  const progress = `${amount(state.done)}${state.total !== null ? ` of ${amount(state.total)}` : ''}`;
  const speed = state.bytes_per_second;
  const timing = state.unit === 'bytes' && speed !== null && speed > 0
    ? ` · ${(speed / 1_000_000).toFixed(1)} MB/s${state.total !== null ? ` · ${Math.ceil(Math.max(0, state.total - state.done) / speed)} seconds left` : ''}` : '';
  return `${state.status === 'cancelling' ? 'Cancelling' : state.phase ? phaseNames[state.phase] : 'Preparing'} · ${progress}${timing}`;
}
export function PuzzleImportFoot({ onOpenSettings }: { onOpenSettings?: () => void }) {
  const controller = usePuzzleImport();
  if (!controller?.desktop || !activeJob(controller.state)) return null;
  const progress = importProgressText(controller.state);
  return <a className="puzzle-import-foot" href="/settings#puzzle-library" aria-label={`Puzzle import: ${progress}. Open Settings import panel`} title={progress} onClick={event => {
    if (onOpenSettings) { event.preventDefault(); onOpenSettings(); }
  }}>
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9M12 7v5l3 2" /></svg>
    <span className="puzzle-import-text">{progress}</span>
  </a>;
}
export function PuzzleLibraryControls() {
  const controller = usePuzzleImport();
  const controlsRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const startRef = useRef<HTMLButtonElement>(null);
  const statusRef = useRef<HTMLParagraphElement>(null);
  const errorRef = useRef<HTMLParagraphElement>(null);
  const previousStatus = useRef(controller?.state.status);
  const status = controller?.state.status;
  const accepted = controller?.state.summary?.accepted ?? 0;
  useEffect(() => {
    if (previousStatus.current === status) return;
    previousStatus.current = status;
    if (document.activeElement !== document.body && !controlsRef.current?.contains(document.activeElement)) return;
    if (status === 'running') cancelRef.current?.focus();
    else if (status === 'succeeded' && accepted > 0) startRef.current?.focus();
    else if (status === 'cancelled' || status === 'failed' || status === 'succeeded') statusRef.current?.focus();
  }, [status, accepted]);
  const requestError = controller?.error;
  useEffect(() => {
    if (requestError && (document.activeElement === document.body || controlsRef.current?.contains(document.activeElement))) errorRef.current?.focus();
  }, [requestError]);
  if (!controller?.desktop) return <><p>Ask the operator to import puzzles into the application database:</p><code>uv run scan64-cli puzzles import /path/to/lichess_db_puzzle.csv.zst --json</code></>;
  const { state, pending, error } = controller;
  const active = activeJob(state);
  const ready = state.status === 'succeeded' && accepted > 0;
  return <div ref={controlsRef}>
    <p>Download the compressed puzzle database from database.lichess.org (about 270 MB; the size changes as the database grows). Keep 50,000 puzzles rated 600–1800. Nothing downloads automatically.</p>
    {active ? <>
      <p>Download → Read → Import → Save</p>
      <ProgressBar label={state.phase ? phaseNames[state.phase] : 'Preparing puzzle import'} value={state.total !== null ? state.done : undefined} max={state.total ?? undefined} valueText={importProgressText(state)} />
      <StatusLine>{state.status === 'cancelling' ? 'Cancelling puzzle import…' : state.phase === 'save' ? 'Saving puzzle catalog…' : `${state.phase ? phaseNames[state.phase] : 'Preparing'} in progress. You can leave this page.`}</StatusLine>
      <Button ref={cancelRef} aria-disabled={pending || state.status === 'cancelling'} onClick={controller.cancel}>{state.phase === 'download' ? 'Cancel download' : 'Cancel import'}</Button>
    </> : <>
      {state.status === 'succeeded' ? <>
        <StatusLine ref={statusRef} tabIndex={-1}>{ready ? <>Puzzle library ready. {state.summary?.inserted.toLocaleString() ?? 0} added; {state.summary?.existing.toLocaleString() ?? 0} already in your library.</> : 'No puzzles matched the filters. Choose another file or download the current database.'}</StatusLine>
        {state.error ? <StatusLine>{state.error}</StatusLine> : null}
        {ready ? <Button ref={startRef} variant="primary" onClick={controller.startPuzzles}>Start puzzles</Button> : null}
      </> : null}
      {state.status === 'cancelled' ? <StatusLine ref={statusRef} tabIndex={-1}>Import cancelled. Your puzzle library is unchanged.</StatusLine> : null}
      {state.status === 'failed' ? <StatusLine ref={statusRef} tabIndex={-1} kind="error">{state.error ?? 'Import failed. Your puzzle library is unchanged. Try again.'}</StatusLine> : null}
      <Button variant={ready ? 'secondary' : 'primary'} aria-disabled={pending} onClick={controller.download}>Download puzzles</Button>
      <Button aria-disabled={pending} onClick={controller.chooseFile}>Choose a file…</Button>
    </>}
    {error ? <StatusLine ref={errorRef} tabIndex={-1} kind="error">{error}</StatusLine> : null}
  </div>;
}
export function PuzzleLibraryPanel() { return <Panel id="puzzle-library" tabIndex={-1} title="Puzzle library"><PuzzleLibraryControls /></Panel>; }
