import { createContext, useContext, useEffect, useRef, useState, type MouseEvent, type ReactNode } from 'react';
import { browserUpdateStatus, checkUpdates, downloadUpdate, isDesktop, openReleaseUrl, restartToUpdate, setAutomaticUpdates, updateStatus, type UpdateStatus } from '../platform';
import { Button, Panel, StatusLine } from './Primitives';
import './Updates.css';

function useUpdater() {
  const [status, setStatus] = useState<UpdateStatus | null>(() => isDesktop() ? null : browserUpdateStatus);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(false);
  const acting = useRef(false);
  useEffect(() => {
    mounted.current = true;
    if (!isDesktop()) return () => { mounted.current = false; };
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function poll() {
      try {
        const next = await updateStatus();
        if (!cancelled) setStatus(next);
      } catch (failure) {
        if (!cancelled) setError(String(failure));
      } finally {
        if (!cancelled) timer = setTimeout(() => { void poll(); }, 1000);
      }
    }
    void poll();
    return () => { cancelled = true; mounted.current = false; clearTimeout(timer); };
  }, []);
  async function act(action: () => Promise<void>) {
    if (acting.current) return;
    acting.current = true;
    setBusy(true);
    setError(null);
    try {
      await action();
      const next = await updateStatus();
      if (mounted.current) setStatus(next);
    } catch (failure) {
      if (mounted.current) setError(String(failure));
    } finally {
      acting.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  async function openRelease(url: string) {
    setError(null);
    try {
      await openReleaseUrl(url);
    } catch (failure) {
      if (mounted.current) setError(String(failure));
    }
  }
  return { status, busy, error, act, openRelease };
}

interface Updater {
  status: UpdateStatus | null;
  busy: boolean;
  error: string | null;
  act: (action: () => Promise<void>) => Promise<void>;
  openRelease: (url: string) => Promise<void>;
}
const UpdatesContext = createContext<Updater | null>(null);
export function UpdatesProvider({ children }: { children: ReactNode }) {
  const updater = useUpdater();
  return <UpdatesContext.Provider value={updater}>{children}</UpdatesContext.Provider>;
}

function statusText(status: UpdateStatus): string {
  switch (status.state) {
    case 'idle': return 'Ready to check for updates.';
    case 'checking': return 'Checking for updates…';
    case 'upToDate': return 'Scan64 is up to date.';
    case 'available': return `Version ${status.availableVersion} is available.`;
    case 'downloading': return `Downloading ${status.availableVersion}${status.percent === null ? '…' : ` · ${Math.round(status.percent)}%`}`;
    case 'ready': return `Version ${status.availableVersion} is ready. Restart when you are ready to update.`;
    case 'installing': return 'Installing update and restarting…';
    case 'failed': return 'Update failed.';
    case 'unavailable': return 'In-app updates are unavailable.';
  }
}

function UpdateActions({ updater }: { updater: Updater }) {
  const { status, busy, act } = updater;
  if (!status) return null;
  if (status.state === 'available') return <Button disabled={busy} onClick={() => { void act(downloadUpdate); }}>Download</Button>;
  if (status.state === 'ready') return <Button disabled={busy} onClick={() => { void act(restartToUpdate); }}>Restart to update</Button>;
  if (status.state === 'failed') return <Button disabled={busy} onClick={() => { void act(checkUpdates); }}>Retry</Button>;
  return null;
}

function UpdatesPanelContent({ updater }: { updater: Updater }) {
  const { status, busy, error, act, openRelease } = updater;
  const unavailable = status?.state === 'unavailable';
  const pending = !status || ['checking', 'downloading', 'installing'].includes(status.state);
  function openLink(event: MouseEvent<HTMLAnchorElement>) {
    if (!isDesktop() || (event.button !== 0 && event.button !== 1)) return;
    event.preventDefault();
    const url = event.currentTarget.getAttribute('href');
    if (url) void openRelease(url);
  }
  return <Panel title="Updates">
    {status ? <>
      <p>Installed version: {status.installedVersion}</p>
      <p>Last checked: {status.lastChecked === null ? 'Never' : <time dateTime={new Date(status.lastChecked).toISOString()}>{new Date(status.lastChecked).toLocaleString()}</time>}</p>
      <p role="status">{statusText(status)}</p>
      {status.reason ? <p>{status.reason}</p> : null}
      {status.state === 'downloading' ? <progress aria-label="Update download" max={100} value={status.percent ?? undefined} /> : null}
    </> : <p role="status">Loading update settings…</p>}
    <div className="updates-actions">
      <Button disabled={busy || pending || unavailable} onClick={() => { void act(checkUpdates); }}>Check for updates</Button>
      <UpdateActions updater={updater} />
    </div>
    <label className="updates-automatic"><input type="checkbox" role="switch" checked={status?.automatic ?? false} disabled={!status || busy || unavailable} onChange={(event) => { const enabled = event.target.checked; void act(() => setAutomaticUpdates(enabled)); }} />Download updates automatically</label>
    <p className="muted">Updates download in the background. Scan64 only restarts when you choose Restart to update.</p>
    {status ? <p><a href={status.releaseNotesUrl} target="_blank" rel="noreferrer" onClick={openLink} onAuxClick={openLink}>Release notes</a>{unavailable ? <> · <a href={status.downloadUrl} target="_blank" rel="noreferrer" onClick={openLink} onAuxClick={openLink}>Download Scan64</a></> : null}</p> : null}
    {error ? <StatusLine kind="error">{error}</StatusLine> : null}
  </Panel>;
}
function StandaloneUpdatesPanel() {
  return <UpdatesPanelContent updater={useUpdater()} />;
}
export function UpdatesPanel() {
  const updater = useContext(UpdatesContext);
  return updater ? <UpdatesPanelContent updater={updater} /> : <StandaloneUpdatesPanel />;
}
export function UpdatesFoot() {
  const updater = useContext(UpdatesContext);
  if (!updater?.status || !['downloading', 'ready', 'available'].includes(updater.status.state)) return null;
  const { status } = updater;
  return <div className="updates-foot" aria-label="App update">
    <p role="status">{status.state === 'ready' ? `Version ${status.availableVersion} is ready` : statusText(status)}</p>
    <UpdateActions updater={updater} />
    {updater.error ? <p role="alert">{updater.error}</p> : null}
  </div>;
}
