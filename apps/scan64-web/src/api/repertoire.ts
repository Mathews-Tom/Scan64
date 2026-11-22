import { apiBase, apiFetch } from '../platform';
import { ApiRequestError, ensurePlayerAuthorization, getOrCreatePlayerId, getPlayerAuthorizationHeader } from './client';
import type { AnalysisTree } from '../components/analysisTree';

export interface RepertoireDecision {
  node_id: number;
  reviewed: boolean;
  due: boolean;
  next_review_at: string | null;
}

export interface RepertoireRead {
  id: string;
  title: string;
  tree: AnalysisTree;
  decisions: RepertoireDecision[];
  total_decisions: number;
  reviewed_decisions: number;
  due_decisions: number;
}

export interface RepertoireAttemptInput {
  attempt_id: string;
  node_id: number;
  submitted_move: string;
}

export interface RepertoireAttemptRead {
  attempt_id: string;
  success: boolean;
  rating: number;
  next_review_at: string;
  repertoire: RepertoireRead;
}

async function request<T>(path: string, method: 'GET' | 'POST', body?: object): Promise<T> {
  const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
  const response = await apiFetch(`${apiBase()}/repertoires${path}`, {
    method,
    headers: { ...getPlayerAuthorizationHeader(playerId), ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  const result: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = result && typeof result === 'object' && 'detail' in result ? result.detail : null;
    throw new ApiRequestError(typeof detail === 'string' ? detail : `Repertoire request failed (${response.status})`, response.status);
  }
  return result as T;
}

export const RepertoireApi = {
  list: () => request<RepertoireRead[]>('', 'GET'),
  importPgn: (pgn: string) => request<RepertoireRead[]>('', 'POST', { pgn }),
  review: (id: string, attempt: RepertoireAttemptInput) => request<RepertoireAttemptRead>(`/${encodeURIComponent(id)}/attempts`, 'POST', attempt),
};
