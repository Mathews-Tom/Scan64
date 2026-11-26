import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiClient, ApiRequestError } from './client';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); });
describe('getPositionInsight', () => {
  it('posts the session source with its player bearer token', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    const result = { step: 3, fen: 'position', candidates: [], visualizations: [] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => result });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.getPositionInsight({ player_id: 'p', play_session_id: 's', step: 3 })).toEqual(result);
    expect(fetch).toHaveBeenCalledWith('/v1/insights/position', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer secret' },
      body: JSON.stringify({ player_id: 'p', play_session_id: 's', step: 3 }),
    });
  });
  it('surfaces endpoint failures', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: 'Busy', json: async () => ({ detail: 'Hint engine is disabled' }) }));
    await expect(ApiClient.getPositionInsight({ player_id: 'p', fen: 'position', step: 1 })).rejects.toBeInstanceOf(ApiRequestError);
    await expect(ApiClient.getPositionInsight({ player_id: 'p', fen: 'position', step: 2 })).rejects.toThrow('Hint engine is disabled');
  });
});

describe('analyzePosition', () => {
  it('posts a standalone FEN with the player bearer token and preserves typed lines', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    const result = { fen: 'position', lines: [{ score_cp: null, mate: -3, moves: [{ uci: 'e7e5', san: 'e5' }] }] };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => result });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.analyzePosition({ player_id: 'p', fen: 'position' })).toEqual(result);
    expect(fetch).toHaveBeenCalledWith('/v1/analysis/position', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer secret' },
      body: JSON.stringify({ player_id: 'p', fen: 'position' }),
    });
  });

  it('preserves busy status and the retryable server explanation', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    const fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, statusText: 'Busy', json: async () => ({ detail: 'Engine busy. Retry shortly.' }) });
    vi.stubGlobal('fetch', fetch);
    await expect(ApiClient.analyzePosition({ player_id: 'p', fen: 'position' })).rejects.toMatchObject({
      status: 503, message: 'Failed to analyse position: Engine busy. Retry shortly.',
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe('explainPosition', () => {
  it('posts the FEN and optional budgets with the player bearer token', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    const result = {
      fen: 'position', source: 'template', fallback_reason: null, text: 'White to move.', lines: [],
      claims: [{ text: 'White to move.', evidence_ref: 'line-1', line_id: 'line-1', moves: ['e2e4'] }], facts: {},
    };
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => result });
    vi.stubGlobal('fetch', fetch);
    expect(await ApiClient.explainPosition({ player_id: 'p', fen: 'position', multipv: 3 })).toEqual(result);
    expect(fetch).toHaveBeenCalledWith('/v1/analysis/position/explanation', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer secret' },
      body: JSON.stringify({ player_id: 'p', fen: 'position', multipv: 3 }),
    });
  });

  it('preserves the status and server detail of a rejected request', async () => {
    localStorage.setItem('scan64_player_token:p', 'secret');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 422, statusText: 'Unprocessable', json: async () => ({ detail: 'multipv exceeds the 4-line analysis limit' }) }));
    await expect(ApiClient.explainPosition({ player_id: 'p', fen: 'position', multipv: 9 })).rejects.toMatchObject({
      status: 422, message: 'Failed to explain engine lines: multipv exceeds the 4-line analysis limit',
    });
  });
});
