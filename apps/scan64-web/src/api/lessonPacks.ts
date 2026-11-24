import { apiBase, apiFetch } from '../platform';
import { ApiRequestError, ensurePlayerAuthorization, getOrCreatePlayerId, getPlayerAuthorizationHeader } from './client';

export interface PackRead {
  id: string; title: string; author: string; licence: string; version: string;
  description: string | null; source: string | null; sha256: string; installed_at: string;
  state: 'pending' | 'verifying' | 'verified' | 'failed' | 'unavailable'; reason: string | null;
  lessons: { id: string; title: string; state: 'pending' | 'verified' | 'rejected'; failing_ply: number | null; reason: string | null }[];
}
export interface LessonRead {
  id: string; title: string; pack_id: string; pack_title: string; author: string;
  fen: string; objective: string; themes: string[];
}
export interface PackCheckRead {
  accepted: boolean; fen: string; ply: number; reply: string | null; complete: boolean;
  hints: string[]; explanation: string | null;
}
export const MAX_PACK_BYTES = 512 * 1024;
async function request<T>(path: string, method = 'GET', body?: BodyInit): Promise<T> {
  const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
  const response = await apiFetch(`${apiBase()}/lesson-packs${path}`, {
    method, headers: { ...getPlayerAuthorizationHeader(playerId), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
    ...(body !== undefined ? { body } : {}),
  });
  if (response.status === 204) return undefined as T;
  const result: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = result && typeof result === 'object' && 'detail' in result ? result.detail : null;
    throw new ApiRequestError(typeof detail === 'string' ? detail : `Lesson pack request failed (${response.status})`, response.status);
  }
  return result as T;
}
const packPath = (id: string) => `/${encodeURIComponent(id)}`;
export const LessonPacksApi = {
  list: () => request<PackRead[]>(''),
  get: (id: string) => request<PackRead>(packPath(id)),
  install: (file: File) => {
    if (file.size > MAX_PACK_BYTES) return Promise.reject(new Error('Lesson pack files must be at most 512 KiB.'));
    return request<PackRead>('', 'POST', file);
  },
  retry: (id: string) => request<PackRead>(`${packPath(id)}/retry`, 'POST'),
  uninstall: (id: string) => request<void>(packPath(id), 'DELETE'),
  lesson: (id: string, lessonId: string) => request<LessonRead>(`${packPath(id)}/lessons/${encodeURIComponent(lessonId)}`),
  check: (id: string, lessonId: string, input: { ply: number; move?: string; hint?: boolean }) =>
    request<PackCheckRead>(`${packPath(id)}/lessons/${encodeURIComponent(lessonId)}/check`, 'POST', JSON.stringify(input)),
};
