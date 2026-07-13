import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient } from './client';

describe('ApiClient', () => {
  const mockFetch = vi.fn();
  
  beforeEach(() => {
    global.fetch = mockFetch;
  });
  
  afterEach(() => {
    vi.resetAllMocks();
  });

  it('createGame calls POST /v1/games', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '123', pgn: '...', white: 'W', black: 'B', result: '*' }),
    });

    const res = await ApiClient.createGame({ pgn: '...' });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pgn: '...' }),
    });
    expect(res.id).toBe('123');
  });

  it('getLearningOpportunities calls GET /v1/games/{id}/learning-opportunities', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ([{ lesson_id: 'abc' }]),
    });

    const res = await ApiClient.getLearningOpportunities('123');
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/games/123/learning-opportunities');
    expect(res[0].lesson_id).toBe('abc');
  });

  it('createPlaySession calls POST /v1/play-sessions', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'sess-1' }),
    });

    const res = await ApiClient.createPlaySession('1500');
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/play-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opponent: '1500' }),
    });
    expect(res.id).toBe('sess-1');
  });

  it('makePlaySessionMove calls POST /v1/play-sessions/{id}/moves', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'sess-1', status: 'active' }),
    });

    const res = await ApiClient.makePlaySessionMove('sess-1', { lan: 'e2e4' });
    expect(mockFetch).toHaveBeenCalledWith('/api/v1/play-sessions/sess-1/moves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ lan: 'e2e4' }),
    });
    expect(res.status).toBe('active');
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      statusText: 'Bad Request',
    });

    await expect(ApiClient.createGame({ pgn: '' })).rejects.toThrow('Failed to create game: Bad Request');
  });
});
