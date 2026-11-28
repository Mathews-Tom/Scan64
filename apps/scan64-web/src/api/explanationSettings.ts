import { apiBase, apiFetch } from '../platform';
import { getPlayerAuthorizationHeader, getStoredPlayerId } from './client';

/** Narrator voices (#387): tone of the plain words around the engine facts. */
export const NARRATOR_VOICES = ['coach', 'terse', 'playful'] as const;
export type NarratorVoice = typeof NARRATOR_VOICES[number];
export const DEFAULT_NARRATOR_VOICE: NarratorVoice = 'coach';

export interface ExplanationSettings {
  mode: 'disabled' | 'local' | 'operator';
  model: string | null;
  /** Saved with a local grant; null or absent otherwise (servers before #387 omit it). */
  voice?: NarratorVoice | null;
}

export interface LocalExplanationProbe {
  available: boolean;
  models: string[];
  /** The default narrator model (#429); servers before #429 omit it. */
  recommended_model?: string;
}

/** Choose the saved model, else the recommended one, else the first listed (#429). */
export function initialModel(probe: LocalExplanationProbe, saved: string | null | undefined): string {
  if (saved && probe.models.includes(saved)) return saved;
  if (probe.recommended_model && probe.models.includes(probe.recommended_model)) return probe.recommended_model;
  return probe.models[0] ?? '';
}

export function storedExplanationOwner(): string | null {
  try {
    const playerId = getStoredPlayerId();
    if (playerId) getPlayerAuthorizationHeader(playerId);
    return playerId;
  } catch {
    return null;
  }
}

async function request<T>(playerId: string, method: 'GET' | 'POST' | 'PUT', signal: AbortSignal, data?: { model: string | null; consent: boolean; voice?: NarratorVoice }): Promise<T> {
  if (storedExplanationOwner() !== playerId) throw new Error('Open Settings with your active player identity.');
  const response = await apiFetch(`${apiBase()}/players/${encodeURIComponent(playerId)}/explanation-settings${method === 'POST' ? '/probe' : ''}`, {
    method,
    signal,
    headers: { ...getPlayerAuthorizationHeader(playerId), ...(data ? { 'Content-Type': 'application/json' } : {}) },
    ...(data ? { body: JSON.stringify(data) } : {}),
  });
  if (!response.ok) throw new Error(`Settings request failed (${response.status}). Check that Scan64 is running and your player is signed in, then reopen Settings.`);
  return await response.json() as T;
}

export function readExplanationSettings(playerId: string, signal: AbortSignal): Promise<ExplanationSettings> {
  return request(playerId, 'GET', signal);
}

export function probeLocalExplanations(playerId: string, signal: AbortSignal): Promise<LocalExplanationProbe> {
  return request(playerId, 'POST', signal);
}

export function saveExplanationSettings(playerId: string, model: string | null, signal: AbortSignal, voice?: NarratorVoice): Promise<ExplanationSettings> {
  return request(playerId, 'PUT', signal, { model, consent: model !== null, ...(model !== null && voice ? { voice } : {}) });
}
