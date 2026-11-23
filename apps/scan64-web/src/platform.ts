import { invoke, isTauri } from '@tauri-apps/api/core';
import { listen, type UnlistenFn } from '@tauri-apps/api/event';
import { createActivePlayer } from './api/client';

interface DesktopConnection {
  port: number;
  token: string;
  smoke: boolean;
  smoke_board?: boolean;
  smoke_engine?: boolean;
}

let connection: DesktopConnection | null = null;

export function isDesktop(): boolean {
  return isTauri();
}

export function apiBase(): string {
  if (!isDesktop()) return '/v1';
  if (connection === null) throw new Error('The desktop server is not connected');
  return `http://127.0.0.1:${connection.port}/v1`;
}

/** Keep player bearer authorization separate from the desktop shell credential. */
export function apiFetch(url: string, options?: RequestInit): Promise<Response> {
  if (!isDesktop()) return options === undefined ? fetch(url) : fetch(url, options);
  const base = apiBase();
  if (url !== base && !url.startsWith(`${base}/`) && !url.startsWith(`${base}?`)) {
    throw new Error('Desktop API requests must use the connected loopback server');
  }
  const headers = new Headers(options?.headers);
  headers.set('X-Scan64-Shell-Token', connection!.token);
  return fetch(url, { ...options, headers });
}

async function connect(command: 'desktop_connection' | 'restart_server'): Promise<void> {
  try {
    connection = await invoke<DesktopConnection>(command);
  } catch (error) {
    connection = null;
    // The shell's error includes the server log path; preserve it for the failure screen.
    throw error;
  }
}

/** Call before mounting screens: their first request must already have the port and token. */
export async function initializeDesktop(): Promise<void> {
  if (!isDesktop()) return;
  await connect('desktop_connection');
  if (!connection?.smoke) return;

  // This is a real page-origin HTTP round trip, not a Rust HTTP proxy or readiness probe.
  const path = '/v1/players';
  // Custom-scheme WebViews may report location.origin as "null".
  const origin = `${window.location.protocol}//${window.location.host}`;
  try {
    const response = await apiFetch(`${apiBase()}/players`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: crypto.randomUUID(), display_name: 'Desktop smoke' }),
      signal: AbortSignal.timeout(15_000),
    });
    if (!response.ok) throw new Error(`Desktop smoke request failed: HTTP ${response.status}`);
    const player = await response.json() as { id: string; access_token: string };
    if (connection.smoke_engine) {
      // Engines are lazy: play a real move before proving that killing the shell
      // tears down its live engine children, rather than checking an empty tree.
      const session = await apiFetch(`${apiBase()}/play-sessions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${player.access_token}`,
        },
        body: JSON.stringify({
          player_id: player.id, player_color: 'black',
          opponent_config: { provider: 'stockfish', bot_level: '1' },
        }),
        signal: AbortSignal.timeout(60_000),
      });
      if (!session.ok) throw new Error(`Desktop engine smoke failed: HTTP ${session.status}`);
      const move = (await session.json() as { opponent_move: unknown }).opponent_move;
      if (typeof move !== 'string' || !/^[a-h][1-8][a-h][1-8][qrbn]?$/.test(move)) {
        throw new Error('Desktop engine smoke did not receive a legal-shaped bot move');
      }
    }
    if (connection.smoke_board) {
      // Exercise the ordinary analysis screen in the installed WebView for CI's screenshot.
      await createActivePlayer('Desktop board smoke');
      window.history.replaceState({}, '', '/analysis');
    }
    await invoke('smoke_complete', { origin, path, status: response.status });
  } catch (error) {
    const reason = String(error).replaceAll(connection.token, '[redacted]');
    await invoke('smoke_failed', { origin, reason });
    throw error;
  }
}

/** Poll the shell's child-process state, without another HTTP health-check convention. */
export async function refreshDesktopConnection(): Promise<void> {
  if (isDesktop()) await connect('desktop_connection');
}

export async function restartDesktopServer(): Promise<void> {
  if (isDesktop()) await connect('restart_server');
}

/** Native paths never enter browser installs; cancellation is not an error. */
export async function choosePuzzleFile(): Promise<string | null> {
  if (!isDesktop()) return null;
  try {
    return await invoke<string | null>('choose_puzzle_file');
  } catch (error) {
    throw typeof error === 'string' ? new Error(error) : error;
  }
}

export async function saveCoachPacket(packet: ArrayBuffer): Promise<void> {
  try {
    await invoke('save_coach_packet', packet);
  } catch (error) {
    throw typeof error === 'string' ? new Error(error) : error;
  }
}

export interface UpdateStatus {
  revision: number;
  state: 'idle' | 'checking' | 'upToDate' | 'available' | 'downloading' | 'ready' | 'installing' | 'failed' | 'unavailable';
  installedVersion: string;
  availableVersion: string | null;
  percent: number | null;
  lastChecked: number | null;
  automatic: boolean;
  reason: string | null;
  downloadUrl: string;
  releaseNotesUrl: string;
}

export const browserUpdateStatus: UpdateStatus = {
  revision: 0, state: 'unavailable', installedVersion: 'Browser version', availableVersion: null,
  percent: null, lastChecked: null, automatic: false,
  reason: 'In-app updates are available in the Scan64 desktop app. Your browser uses the version served by your Scan64 server.',
  downloadUrl: 'https://github.com/Mathews-Tom/Scan64/releases/latest',
  releaseNotesUrl: 'https://github.com/Mathews-Tom/Scan64/releases/latest',
};

export async function updateStatus(): Promise<UpdateStatus> {
  return isDesktop() ? invoke<UpdateStatus>('update_status') : browserUpdateStatus;
}

/** Listen before reading the snapshot so updates during bootstrap cannot be lost. */
export async function subscribeUpdateStatus(onStatus: (status: UpdateStatus) => void, signal: AbortSignal): Promise<() => void> {
  let disposed = false;
  let unlisten: UnlistenFn | undefined;
  let revision = -1;
  function cleanup() {
    if (disposed) return;
    disposed = true;
    signal.removeEventListener('abort', cleanup);
    const stop = unlisten;
    unlisten = undefined;
    stop?.();
  }
  function receive(status: UpdateStatus) {
    if (disposed || status.revision <= revision) return;
    revision = status.revision;
    onStatus(status);
  }
  if (signal.aborted) {
    cleanup();
    return cleanup;
  }
  signal.addEventListener('abort', cleanup, { once: true });
  try {
    if (!isDesktop()) {
      receive(browserUpdateStatus);
      return cleanup;
    }
    const stop = await listen<UpdateStatus>('scan64-update-status', (event) => receive(event.payload));
    if (disposed) {
      stop();
      return cleanup;
    }
    unlisten = stop;
    receive(await updateStatus());
    return cleanup;
  } catch (error) {
    cleanup();
    throw error;
  }
}

export async function checkUpdates(): Promise<UpdateStatus> {
  return isDesktop() ? invoke<UpdateStatus>('check_updates') : browserUpdateStatus;
}

export async function downloadUpdate(): Promise<UpdateStatus> {
  return isDesktop() ? invoke<UpdateStatus>('download_update') : browserUpdateStatus;
}

export async function restartToUpdate(): Promise<UpdateStatus> {
  return isDesktop() ? invoke<UpdateStatus>('restart_to_update') : browserUpdateStatus;
}

export async function setAutomaticUpdates(enabled: boolean): Promise<UpdateStatus> {
  return isDesktop() ? invoke<UpdateStatus>('set_automatic_updates', { enabled }) : browserUpdateStatus;
}

/** Desktop links are opened by the shell; browser anchors retain their normal navigation. */
export async function openReleaseUrl(url: string): Promise<void> {
  if (isDesktop()) await invoke('open_release_url', { url });
}

const FEEDBACK_ISSUE_URL = 'https://github.com/Mathews-Tom/Scan64/issues/new';
/** Same bound as the desktop shell: longer request lines are rejected by browsers and GitHub. */
export const FEEDBACK_URL_LIMIT = 8000;

/** Percent-encode everything except RFC 3986 unreserved characters, exactly as the desktop shell does. */
function encodeStrict(value: string): string {
  return encodeURIComponent(value).replace(/[!'()*]/g, character => `%${character.charCodeAt(0).toString(16).toUpperCase()}`);
}

/** Browser anchors open this prefilled form; the desktop shell builds the same URL itself. */
export function feedbackIssueUrl(title: string, body: string): string {
  return `${FEEDBACK_ISSUE_URL}?title=${encodeStrict(title)}&body=${encodeStrict(body)}`;
}

/** Desktop only: the shell validates the text and opens GitHub's form in the system browser. */
export async function openFeedbackIssue(title: string, body: string): Promise<void> {
  try {
    await invoke('open_feedback_issue', { title, body });
  } catch (error) {
    throw typeof error === 'string' ? new Error(error) : error;
  }
}
