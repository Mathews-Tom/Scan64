import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient, getPlayerAuthorizationHeader } from './client';

describe('ApiClient', () => {
  const mockFetch = vi.fn();
  
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    localStorage.clear();
  });
  
  afterEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
  });

  it('createGame calls POST /v1/games', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '123', pgn: '...', white: 'W', black: 'B', result: '*' }),
    });

    const res = await ApiClient.createGame({ pgn: '...' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pgn: '...' }),
    });
    expect(res.id).toBe('123');
  });
  it('getLearningOpportunities calls GET /v1/games/{id}/learning-opportunities', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [{ lesson_id: 'abc' }] }),
    });

    const res = await ApiClient.getLearningOpportunities('123');
    expect(mockFetch).toHaveBeenCalledWith('/v1/games/123/learning-opportunities');
    expect(res[0].lesson_id).toBe('abc');
  });

  it('createPlaySession calls POST /v1/play-sessions', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'sess-1' }),
    });

    const res = await ApiClient.createPlaySession({ player_id: 'test', opponent_config: { strength: '1500' } });
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_id: 'test', opponent_config: { strength: '1500' } }),
    });
    expect(res.id).toBe('sess-1');
  });

  it('makePlaySessionMove calls POST /v1/play-sessions/{id}/moves', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ opponent_move: 'e7e5' }),
    });

    const res = await ApiClient.makePlaySessionMove('sess-1', { move: 'e2e4' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions/sess-1/moves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ move: 'e2e4' }),
    });
    expect(res.opponent_move).toBe('e7e5');
  });

  it('stores a player token outside the public player result', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'player-1', preferences: {}, access_token: 'token-1' }),
    });

    const player = await ApiClient.createPlayer({ id: 'player-1' });

    expect(player).toEqual({ id: 'player-1', preferences: {} });
    expect(getPlayerAuthorizationHeader('player-1')).toEqual({
      Authorization: 'Bearer token-1',
    });
  });

  it('sends the player bearer token for player reports', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch
      .mockResolvedValueOnce({ ok: true, json: async () => ({ player_id: 'player-1' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ player_id: 'player-1' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ player_id: 'player-1' }) })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ player_id: 'player-1' }) });

    await Promise.all([
      ApiClient.getPlayerProfile('player-1'),
      ApiClient.getPlayerProgress('player-1'),
      ApiClient.getPlayerPatterns('player-1'),
      ApiClient.getPlayerEvidence('player-1'),
    ]);

    for (const resource of ('profile progress patterns evidence').split(' ')) {
      expect(mockFetch).toHaveBeenCalledWith(`/v1/players/player-1/${resource}`, {
        headers: { Authorization: 'Bearer token-1' },
      });
    }
  });

  it('throws on non-ok response', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: false,
      statusText: 'Bad Request',
    });

    await expect(ApiClient.createGame({ pgn: '' })).rejects.toThrow('Failed to create game: Bad Request');
  });
});
