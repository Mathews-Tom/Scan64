import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { ApiClient, ApiRequestError, BotPlayUnavailableError, BOT_PLAY_UNAVAILABLE_MESSAGE, LessonAttemptLimitError, createActivePlayer, getPlayerAuthorizationHeader, getStoredPlayerId } from './client';
import { onboardingFixture, strengthFixture } from '../components/strength.test-fixtures';

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

  it.each([404, 409, 503])('preserves rejected assistance status %s without retrying or borrowing another actor token', async status => {
    localStorage.setItem('scan64_player_id', 'other-player');
    localStorage.setItem('scan64_player_token:other-player', 'other-token');
    localStorage.setItem('scan64_player_token:player-1', 'owner-token');
    mockFetch.mockResolvedValueOnce({
      ok: false, status, statusText: 'Rejected', json: async () => ({ detail: 'Assistance unavailable' }),
    });
    await expect(ApiClient.gradePlayMoves('session-1', {
      player_id: 'player-1', fen: '8/8/8/8/8/8/8/K6k w - - 0 1', moves: ['a1a2'], mode: 'guard',
    })).rejects.toMatchObject({ status, message: 'Could not grade moves: Assistance unavailable' });
    expect(mockFetch).toHaveBeenCalledTimes(1);
    expect(mockFetch.mock.calls[0][1].headers.Authorization).toBe('Bearer owner-token');
    expect(localStorage.getItem('scan64_player_id')).toBe('other-player');
  });

  it.each([404, 409, 500])('keeps bot recovery bound to its explicit actor and surfaces status %s without replay', async status => {
    localStorage.setItem('scan64_player_id', 'other-player');
    localStorage.setItem('scan64_player_token:other-player', 'other-token');
    localStorage.setItem('scan64_player_token:owner', 'owner-token');
    mockFetch.mockResolvedValueOnce({ ok: false, status, statusText: 'Rejected' });
    await expect(ApiClient.resumePlaySession('session-1', 'owner')).rejects.toMatchObject({ status });
    expect(mockFetch).toHaveBeenCalledExactlyOnceWith('/v1/play-sessions/session-1/resume', {
      method: 'POST', headers: { Authorization: 'Bearer owner-token' },
    });
    expect(localStorage.getItem('scan64_player_id')).toBe('other-player');
  });

  it('refuses assistance when the requested actor has no stored token', async () => {
    localStorage.setItem('scan64_player_id', 'other-player');
    localStorage.setItem('scan64_player_token:other-player', 'other-token');
    await expect(ApiClient.gradePlayMoves('session-1', {
      player_id: 'player-1', fen: '8/8/8/8/8/8/8/K6k w - - 0 1', moves: ['a1a2'], mode: 'guard',
    })).rejects.toThrow('No access token is stored for player player-1');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('reads owned onboarding and strength with the stored token', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => onboardingFixture });
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => strengthFixture });
    await expect(ApiClient.getPlayerOnboarding('player-1')).resolves.toEqual(onboardingFixture);
    await expect(ApiClient.getPlayerStrength('player-1')).resolves.toEqual(strengthFixture);
    expect(mockFetch).toHaveBeenNthCalledWith(1, '/v1/players/player-1/onboarding', { headers: { Authorization: 'Bearer token-1' } });
    expect(mockFetch).toHaveBeenNthCalledWith(2, '/v1/players/player-1/strength', { headers: { Authorization: 'Bearer token-1' } });
  });

  it('writes declarations and explicit manual choices only through owned endpoints', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValue({ ok: true, json: async () => onboardingFixture });
    await ApiClient.updatePlayerOnboarding('player-1', { action: 'declare', declared_level: 'club' });
    await ApiClient.setPlayerBotChoice('player-1', 8);
    expect(mockFetch).toHaveBeenNthCalledWith(1, '/v1/players/player-1/onboarding', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ action: 'declare', declared_level: 'club' }),
    });
    expect(mockFetch).toHaveBeenNthCalledWith(2, '/v1/players/player-1/bot-choice', {
      method: 'PUT', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ bot_level: 8 }),
    });
  });

  it('preserves shared not-found errors for rejected onboarding writes', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValue({ ok: false, status: 404, statusText: 'Not Found' });
    await expect(ApiClient.updatePlayerOnboarding('player-1', { action: 'skip' })).rejects.toMatchObject({ status: 404 });
    await expect(ApiClient.setPlayerBotChoice('player-1', 2)).rejects.toMatchObject({ status: 404 });
    expect(localStorage.getItem('scan64_player_token:player-1')).toBe('token-1');
  });

  it('records a famous game attempt with the submitted player authorization', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    localStorage.setItem('scan64_player_id', 'other-player');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'attempt-1', success: true, hint_assisted: false }),
    });
    const attempt = {
      player_id: 'player-1', decision_id: 'opera-open-lines',
      hint_assisted: false, response_payload: { move: 'Nxb5' },
    };
    await ApiClient.recordFamousGameAttempt('morphy-opera-1858', attempt);
    expect(mockFetch).toHaveBeenCalledWith('/v1/content/famous-games/morphy-opera-1858/attempts', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify(attempt),
    });
  });

  it('does not send a famous game attempt without stored player authorization', async () => {
    await expect(ApiClient.recordFamousGameAttempt('morphy-opera-1858', {
      player_id: 'player-1', decision_id: 'opera-open-lines',
      hint_assisted: false, response_payload: { move: 'Nxb5' },
    })).rejects.toThrow('No access token is stored for player player-1');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('reads the stored identity without creating one or throwing when absent', () => {
    expect(getStoredPlayerId()).toBeNull();
    expect(localStorage.getItem('scan64_player_id')).toBeNull();
    localStorage.setItem('scan64_player_id', 'stored-player');
    expect(getStoredPlayerId()).toBe('stored-player');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('creates a named active identity and stores its authorization only after success', async () => {
    const id = '00000000-0000-0000-0000-000000000001';
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(id);
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id, preferences: {}, access_token: 'named-token' }),
    });
    await expect(createActivePlayer('  Ada  ')).resolves.toBe(id);
    expect(mockFetch).toHaveBeenCalledWith('/v1/players', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id, display_name: 'Ada' }),
    });
    expect(localStorage.getItem('scan64_player_id')).toBe(id);
    expect(getPlayerAuthorizationHeader(id)).toEqual({ Authorization: 'Bearer named-token' });
  });

  it('does not store an active identity for failed or conflicting registration', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 409 });
    await expect(createActivePlayer('Ada')).rejects.toThrow('Could not create a new player identity');
    expect(localStorage.getItem('scan64_player_id')).toBeNull();
    mockFetch.mockResolvedValueOnce({ ok: false, status: 503, statusText: 'Unavailable' });
    await expect(createActivePlayer('Ada')).rejects.toThrow('Failed to create player');
    expect(localStorage.getItem('scan64_player_id')).toBeNull();
  });

  it('does not register a blank display name', async () => {
    await expect(createActivePlayer('   ')).rejects.toThrow('Enter a display name');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it('requests the weekly report with player authorization and encoded identity', async () => {
    localStorage.setItem('scan64_player_token:player /1', 'token-1');
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ active_mastery: [] }) });
    await ApiClient.getWeeklyReport('player /1');
    expect(mockFetch).toHaveBeenCalledWith('/v1/reports/weekly?player_id=player%20%2F1', {
      headers: { Authorization: 'Bearer token-1' },
    });
  });

  it('surfaces the server detail when analysis rejects a legacy game', async () => {
    localStorage.setItem('scan64_active_player_id', 'player-1');
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({ detail: 'Cannot analyse a legacy game without owner_color' }),
    });
    await expect(ApiClient.createAnalysisJob('legacy-game')).rejects.toThrow('Cannot analyse a legacy game without owner_color');
  });

  it('createGame calls POST /v1/games', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: '123', pgn: '...', white: 'W', black: 'B', result: '*' }),
    });

    const res = await ApiClient.createGame({ owner_color: 'white',  pgn: '...', player_id: 'player-1' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/games', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ owner_color: 'white',  pgn: '...', player_id: 'player-1' }),
    });
    expect(res.id).toBe('123');
  });

  it.each(['lichess', 'chesscom'] as const)('imports %s under the active token without sending a player id', async source => {
    localStorage.setItem('scan64_player_id', 'recovered-owner');
    localStorage.setItem('scan64_player_token:recovered-owner', 'owner-token');
    const result = { imported_count: 0, duplicate_count: 2, skipped_count: 1, games: [], message: 'Already imported.' };
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => result });
    await expect(ApiClient.importOnlineGames({ source, username: 'Alice', max_games: 3 })).resolves.toEqual(result);
    expect(mockFetch).toHaveBeenCalledExactlyOnceWith('/v1/games/import-online', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer owner-token' },
      body: JSON.stringify({ source, username: 'Alice', max_games: 3 }),
    });
  });

  it('refuses online import without an authorized active owner', async () => {
    await expect(ApiClient.importOnlineGames({ source: 'lichess', username: 'Alice', max_games: 3 }))
      .rejects.toThrow('Player authorization is required');
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it.each([
    [404, 'Lichess username not found.'],
    [429, 'Chess.com rate limit reached. Try again later.'],
    [502, 'Lichess could not be reached. Try again.'],
    [422, 'Malformed PGN from Chess.com.'],
  ])('preserves online import error %s and detail', async (status, detail) => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({ ok: false, status, json: async () => ({ detail }) });
    await expect(ApiClient.importOnlineGames({ source: 'lichess', username: 'Alice', max_games: 3 }))
      .rejects.toMatchObject({ message: detail, status });
  });

  it('explains a local network failure without hiding the selected source', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await expect(ApiClient.importOnlineGames({ source: 'chesscom', username: 'Alice', max_games: 3 }))
      .rejects.toThrow('Could not reach Scan64 to import from Chess.com');
  });

  it('getGame calls GET /v1/games/{id}', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'game-1', pgn: '1. e4 e5', white: 'White', black: 'Black', result: '*' }),
    });

    const game = await ApiClient.getGame('game-1');

    expect(mockFetch).toHaveBeenCalledWith('/v1/games/game-1', {
      headers: { Authorization: 'Bearer token-1' },
    });
    expect(game.pgn).toBe('1. e4 e5');
  });

  it('gets an owned game analysis status', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: 'completed' }),
    });

    const status = await ApiClient.getGameAnalysisStatus('game-1');

    expect(mockFetch).toHaveBeenCalledWith('/v1/games/game-1/analysis-status', {
      headers: { Authorization: 'Bearer token-1' },
    });
    expect(status.status).toBe('completed');
  });

  it('preserves a failed game request status', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' });

    const request = ApiClient.getGame('game-1');

    await expect(request).rejects.toBeInstanceOf(ApiRequestError);
    await expect(request).rejects.toMatchObject({ status: 404 });
  });

  it('rejects a game read without stored player authorization before fetching', async () => {
    await expect(ApiClient.getGame('game-1')).rejects.toMatchObject({ status: 401 });
    expect(mockFetch).not.toHaveBeenCalled();
  });
  it('serves owned game lessons with player authorization', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: 'study-1', lessons: [{ lesson_id: 'abc' }], next_cursor: null }),
    });

    const res = await ApiClient.getLearningOpportunities('123');
    expect(mockFetch).toHaveBeenCalledWith('/v1/games/123/learning-opportunities?player_id=player-1', {
      headers: { Authorization: 'Bearer token-1' },
    });
    expect(res.session_id).toBe('study-1');
    expect(res.lessons[0].lesson_id).toBe('abc');
  });

  it('recovers a player identity before requesting game learning opportunities', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    vi.spyOn(crypto, 'randomUUID').mockReturnValue('00000000-0000-0000-0000-000000000002');
    mockFetch
      .mockResolvedValueOnce({ ok: false, status: 409, statusText: 'Conflict' })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          id: '00000000-0000-0000-0000-000000000002',
          preferences: {},
          access_token: 'token-2',
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ session_id: 'study-2', lessons: [], next_cursor: null }),
      });

    await ApiClient.getLearningOpportunities('123');

    expect(mockFetch).toHaveBeenNthCalledWith(
      3,
      '/v1/games/123/learning-opportunities?player_id=00000000-0000-0000-0000-000000000002',
      { headers: { Authorization: 'Bearer token-2' } },
    );
  });

  it('createPlaySession calls POST /v1/play-sessions', async () => {
    localStorage.setItem('scan64_player_token:test', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'sess-1' }),
    });

    const res = await ApiClient.createPlaySession({ player_id: 'test', opponent_config: { provider: 'stockfish', bot_level: '1' }, player_color: 'black' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
      body: JSON.stringify({ player_id: 'test', opponent_config: { provider: 'stockfish', bot_level: '1' }, player_color: 'black' }),
    });
    expect(res.id).toBe('sess-1');
  });

  it('posts an owned conversion source without client FEN, colour or original game attachment', async () => {
    localStorage.setItem('scan64_player_token:owner', 'owner-token');
    const request = { player_id: 'owner', opponent_config: { provider: 'stockfish', bot_level: '8' },
      sparring_source: { game_id: 'original-game', ply: 12 } };
    mockFetch.mockResolvedValueOnce({ ok: true, json: async () => ({ id: 'conversion-1', sparring: { seed: 'server-seed' } }) });
    const session = await ApiClient.createPlaySession(request);
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions', {
      method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer owner-token' },
      body: JSON.stringify(request),
    });
    expect(session.sparring?.seed).toBe('server-seed');
  });

  it('preserves conversion eligibility errors and their HTTP status', async () => {
    localStorage.setItem('scan64_player_token:owner', 'owner-token');
    mockFetch.mockResolvedValueOnce({ ok: false, status: 422, statusText: 'Unprocessable Entity',
      json: async () => ({ detail: 'This owned position is not eligible for conversion practice.' }) });
    const request = ApiClient.createPlaySession({ player_id: 'owner',
      opponent_config: { provider: 'stockfish', bot_level: '1' }, sparring_source: { game_id: 'original-game', ply: 12 } });
    await expect(request).rejects.toBeInstanceOf(ApiRequestError);
    await expect(request).rejects.toMatchObject({ status: 422, message: 'This owned position is not eligible for conversion practice.' });
  });

  it('getPlaySession calls GET /v1/play-sessions/{id}', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ id: 'sess-1', player_id: 'player-1', opponent_config: {}, status: 'active' }),
    });

    const session = await ApiClient.getPlaySession('sess-1');
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions/sess-1', {
      headers: { Authorization: 'Bearer token-1' },
    });
    expect(session.status).toBe('active');
  });

  it('preserves a failed play-session request status', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' });

    const request = ApiClient.getPlaySession('sess-1');

    await expect(request).rejects.toBeInstanceOf(ApiRequestError);
    await expect(request).rejects.toMatchObject({ status: 404 });
  });

  it.each([
    ['move', () => ApiClient.makePlaySessionMove('sess-1', { move: 'e2e4' })],
    ['resume', () => ApiClient.resumePlaySession('sess-1', 'player-1')],
    ['opponent move', () => ApiClient.resumePlaySessionOpponentMove('sess-1', 3)],
    ['create', () => ApiClient.createPlaySession({ player_id: 'player-1', opponent_config: {} })],
  ])('maps a 503 bot_play_unavailable %s response to BotPlayUnavailableError', async (_name, call) => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 503, statusText: 'Service Unavailable',
      json: async () => ({ detail: {
        code: 'bot_play_unavailable',
        message: 'Bot play is unavailable under the active engine profile.',
        reason: 'The bot ladder requires UCI option(s) Skill Level, which Ethereal does not declare',
      } }),
    });
    const error = await call().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(BotPlayUnavailableError);
    expect(error).toMatchObject({
      status: 503, message: BOT_PLAY_UNAVAILABLE_MESSAGE, reason: expect.stringContaining('Skill Level'),
    });
  });

  it.each([
    ['policy_mismatch', "This game was started under the bot ladder 'stockfish-error-budget-v1'", 'Start a new game.'],
    ['elo_out_of_range', 'level 1 UCI_Elo 1000, outside the 1350-3450', "Correct the profile's [ladder] elo values."],
  ])('shows the %s advice and the server reason, not the Stockfish advice', async (kind, reason, advice) => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 503, statusText: 'Service Unavailable',
      json: async () => ({ detail: { code: 'bot_play_unavailable', message: 'Bot play is unavailable under the active engine profile.', reason, kind } }),
    });
    const error = await ApiClient.makePlaySessionMove('session-1', { move: 'e2e4' } as never).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(BotPlayUnavailableError);
    expect(error).toMatchObject({ kind, reason });
    expect((error as Error).message).toContain(advice);
    expect((error as Error).message).toContain(reason);
    expect((error as Error).message).not.toContain('Stockfish profile');
  });

  it.each([
    ['analysis board', () => ApiClient.analyzePosition({ player_id: 'player-1', fen: 'startpos' } as never), 'Failed to analyse position'],
    ['hint', () => ApiClient.getPositionInsight({ player_id: 'player-1', fen: 'startpos' } as never), 'Failed to get hint'],
    ['explanation', () => ApiClient.explainPosition({ player_id: 'player-1', fen: 'startpos' } as never), 'Failed to explain engine lines'],
  ])('shows the engine capability message and reason on a 503 %s response', async (_name, call, prefix) => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 503, statusText: 'Service Unavailable',
      json: async () => ({ detail: {
        code: 'engine_capability_unavailable',
        message: 'This feature is unavailable under the active engine profile.',
        reason: '4 analysis lines need the UCI option MultiPV, which NoMultiPV 1 does not declare',
      } }),
    });
    const error = await call().catch((caught: unknown) => caught);
    expect(error).toMatchObject({
      status: 503,
      message: `${prefix}: This feature is unavailable under the active engine profile. `
        + '4 analysis lines need the UCI option MultiPV, which NoMultiPV 1 does not declare.',
    });
  });

  it('keeps other 503 move responses as plain ApiRequestError', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 503, statusText: 'Busy', json: async () => ({ detail: 'busy' }),
    });
    const error = await ApiClient.makePlaySessionMove('sess-1', { move: 'e2e4' }).catch((caught: unknown) => caught);
    expect(error).not.toBeInstanceOf(BotPlayUnavailableError);
    expect(error).toMatchObject({ status: 503, message: 'Failed to make move: Busy' });
  });

  it('makePlaySessionMove calls POST /v1/play-sessions/{id}/moves', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ opponent_move: 'e7e5', status: 'active' }),
    });

    const res = await ApiClient.makePlaySessionMove('sess-1', { move: 'e2e4' });
    expect(mockFetch).toHaveBeenCalledWith('/v1/play-sessions/sess-1/moves', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: 'Bearer token-1' },
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
        json: async () => ({ session_id: 'study-1', lessons: [], move_choices: {}, attempts_used: {} }),
      });

    await ApiClient.getTrainingSession();

    expect(mockFetch).toHaveBeenNthCalledWith(1, '/v1/players', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: 'player-1', display_name: 'Anonymous' }),
    });
    expect(mockFetch).toHaveBeenNthCalledWith(2, '/v1/learning/session?player_id=player-1&mode=review', {
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
        json: async () => ({ session_id: 'study-2', lessons: [], move_choices: {}, attempts_used: {} }),
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
      '/v1/learning/session?player_id=00000000-0000-0000-0000-000000000002&mode=review',
      { headers: { Authorization: 'Bearer token-2' } },
    );
  });

  it('preserves the server attempt limit separately from other lesson conflicts', async () => {
    localStorage.setItem('scan64_player_id', 'player-1');
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    const attempt = { session_id: 'study-1', lesson_id: 'lesson-1', source_kind: 'persisted_opportunity' as const, submitted_move: 'e2e4', elapsed_ms: 10, hints_used: 0 };
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({ detail: { code: 'lesson_attempt_limit_reached', attempts_used: 3 } }),
    });
    await expect(ApiClient.recordLessonAttempt(attempt)).rejects.toMatchObject({
      constructor: LessonAttemptLimitError, status: 409, attemptsUsed: 3,
    });
    mockFetch.mockResolvedValueOnce({
      ok: false, status: 409, statusText: 'Conflict',
      json: async () => ({ detail: 'This drill is no longer available' }),
    });
    await expect(ApiClient.recordLessonAttempt(attempt)).rejects.toMatchObject({
      constructor: ApiRequestError, status: 409,
    });
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
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: async () => { throw new SyntaxError('no body'); },
    });

    await expect(ApiClient.createGame({ owner_color: 'white',  pgn: '', player_id: 'player-1' })).rejects.toThrow('Failed to create game: Bad Request');
  });

  it('reports the server reason when a game cannot be created', async () => {
    localStorage.setItem('scan64_player_token:player-1', 'token-1');
    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      json: async () => ({ detail: "Variant 'Chess960' is not supported; Scan64 supports Standard, Crazyhouse, Three-check, Atomic, Antichess, King of the Hill, Horde, Racing Kings" }),
    });

    await expect(ApiClient.createGame({ owner_color: 'white', pgn: '', player_id: 'player-1' })).rejects.toThrow(
      "Failed to create game: Variant 'Chess960' is not supported; Scan64 supports Standard, Crazyhouse, Three-check, Atomic, Antichess, King of the Hill, Horde, Racing Kings");
  });
});
