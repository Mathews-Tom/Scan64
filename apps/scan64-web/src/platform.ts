import { invoke, isTauri } from '@tauri-apps/api/core';

interface DesktopConnection {
  port: number;
  token: string;
  smoke: boolean;
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
    await response.json();
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
  return invoke<string | null>('choose_puzzle_file');
}
