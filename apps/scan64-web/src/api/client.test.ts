import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient, getPlayerAuthorizationHeader } from './client';

describe('ApiClient', () => {
  const mockFetch = vi.fn();
  
  beforeEach(() => {
    vi.stubGlobal('fetch', mockFetch);
    localStorage.clear();
  });
  
  afterEach(() => {
    vi.restoreAllMocks();
    vi.resetAllMocks();
    localStorage.clear();
  });

  it('createGame calls POST /v1/games', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '123', pgn: '...', white: 'W', black: 'B', result: '*' }),
    });

    const res = await ApiClient.createGame({ pgn: '...', player_id: 'player-1' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pgn: '...', player_id: 'player-1' }),
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
      json: async () => ({ opponent_move: 'e7e5', status: 'active' }),
    });

    const res = await ApiClient.makePlaySessionMove('sess-1', { move: 'e2e4' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions/sess-1/moves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ move: 'e2e4' }),
    });
    expect(res.opponent_move).toBe('e7e5');
    expect(res.status).toBe('active');
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

  it('reuses the identity of an already-registered player', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
    });

    const player = await ApiClient.createPlayer({ id: 'player-1' });

    expect(player).toEqual({ id: 'player-1', preferences: {} });
    expect(getPlayerAuthorizationHeader('player-1')).toEqual({
      Authorization: 'Bearer token-1',
    });
  });

  it('creates a player before requesting a training session without a stored token', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: 'player-1', preferences: {}, access_token: 'token-1' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: 'study-1', lessons: [] }),
      });

    await ApiClient.getTrainingSession();

    expect(mockFetch).toHaveBeenNthCalledWith(1, '/v1/players', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: 'player-1', display_name: 'Anonymous' }),
    });
    expect(mockFetch).toHaveBeenNthCalledWith(2, '/v1/learning/session?player_id=player-1', {
      headers: { Authorization: 'Bearer token-1' },
    });
  });

  it('replaces an identity whose existing player record has no stored token', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('00000000-0000-0000-0000-000000000002');
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 409, statusText: 'Conflict' })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ id: '00000000-0000-0000-0000-000000000002', preferences: {}, access_token: 'token-2' }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: 'study-2', lessons: [] }),
      });

    await ApiClient.getTrainingSession();

    expect(mockFetch).toHaveBeenNthCalledWith(2, '/v1/players', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: '00000000-0000-0000-0000-000000000002',
        display_name: 'Anonymous',
      }),
    });
    expect(localStorage.getItem('scan64_player_id')).toBe(
      '00000000-0000-0000-0000-000000000002',
    );
    expect(mockFetch).toHaveBeenNthCalledWith(
      3,
      '/v1/learning/session?player_id=00000000-0000-0000-0000-000000000002',
      { headers: { Authorization: 'Bearer token-2' } },
    );
  });

  it('preserves the active identity when registration fails without a token', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Server Error',
    });

    await expect(ApiClient.getTrainingSession()).rejects.toThrow(
      'Failed to create player: Server Error',
    );

    expect(localStorage.getItem('scan64_player_id')).toBe('player-1');
    expect(mockFetch).toHaveBeenCalledTimes(1);
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

    await expect(ApiClient.createGame({ pgn: '', player_id: 'player-1' })).rejects.toThrow('Failed to create game: Bad Request');
  });
});
