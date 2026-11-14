import { useEffect, useState } from 'react';
import { ApiClient } from '../api/client';

export function DesktopUpdateBanner() {
  const [readyVersion, setReadyVersion] = useState<string | null>(null);
  const [dismissedVersion, setDismissedVersion] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    async function poll() {
      try {
        const state = await ApiClient.getAppUpdate();
        if (!cancelled) setReadyVersion(state.status === 'ready' ? state.version : null);
      } catch {
        // Update availability must never interrupt chess practice.
      }
      if (!cancelled) timer = window.setTimeout(() => { void poll(); }, 60_000);
    }
    void ApiClient.getAppInfo().then(info => {
      if (!cancelled && info.distribution === 'desktop') void poll();
    }).catch(() => { /* Source/offline sessions have no updater. */ });
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, []);

  if (!readyVersion || readyVersion === dismissedVersion) return null;
  return (
    <aside aria-label="Application update" className="desktop-update-banner">
      <span role="status">Scan64 {readyVersion} is ready — restart from the Scan64 window</span>
      <button type="button" aria-label="Dismiss update notification" onClick={() => setDismissedVersion(readyVersion)}>Dismiss</button>
    </aside>
  );
}
