import type {
  AnalysisJobRead,
  BotRead,
  AttemptCreate,
  AttemptRead,
  CoachDashboard,
  EvidenceReport,
  FamousGameRead,
  HabitSummaryRead,
  GameCreate,
  GameAnalysisStatusRead,
  GameRead,
  GameLearningSessionRead,
  GameReviewRead,
  LessonAttemptCreate,
  LessonAttemptRead,
  PlayMoveCreate,
  PlayMoveResponse,
  PlayerCreate,
  PlayerRead,
  PlayerOnboardingRead,
  OnboardingAction,
  StrengthRead,
  PlayerGamesPage,
  PatternsReport,
  PlayerProfileRead,
  PlayerProgressReport,
  PlaySessionCreate,
  PlaySessionRead,
  PositionRead,
  PositionInsight,
  PositionInsightRequest,
  PuzzleDailyRead,
  PuzzleRunRead,
  PuzzleStartRead,
  PuzzleRunCreate,
  PuzzleModesRead,
  PuzzleMistakesRead,
  PuzzleMistakeStartRead,
  TrainingSessionRead,
  WeeklyReport,
} from './types';

const API_BASE = '/v1';
const PLAYER_TOKEN_STORAGE_PREFIX = 'scan64_player_token:';

const PLAYER_ID_STORAGE_KEY = 'scan64_player_id';

const pendingPlayerAuthorizations = new Map<string, Promise<string>>();

export class ApiRequestError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

export class PuzzleConflictError extends ApiRequestError {
  readonly run: PuzzleRunRead;

  constructor(message: string, run: PuzzleRunRead) {
    super(message, 409);
    this.run = run;
  }
}

type PuzzleExpectedState = { expected_cursor: number; expected_revision: number };

interface PlayerRegistration {
  player: PlayerRead;
  issuedToken: boolean;
}

export function getStoredPlayerId(): string | null {
  return localStorage.getItem(PLAYER_ID_STORAGE_KEY) || null;
}

export function getActivePlayerId(): string {
  const playerId = getStoredPlayerId();
  if (!playerId) throw new Error('No active player identity is stored');
  return playerId;
}

export function getOrCreatePlayerId(): string {
  const existingPlayerId = getStoredPlayerId();
  if (existingPlayerId) return existingPlayerId;

  const playerId = crypto.randomUUID();
  localStorage.setItem(PLAYER_ID_STORAGE_KEY, playerId);
  return playerId;
}

async function registerPlayer(data: PlayerCreate): Promise<PlayerRegistration> {
  const response = await fetch(`${API_BASE}/players`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data),
  });
  if (response.status === 409) {
    return { player: { id: data.id, preferences: {} }, issuedToken: false };
  }
  if (!response.ok) {
    throw new Error(`Failed to create player: ${response.statusText}`);
  }
  const json = await response.json() as PlayerRead & { access_token?: unknown };
  if (typeof json.access_token !== 'string') {
    throw new Error('Player creation response did not include an access token');
  }
  localStorage.setItem(`${PLAYER_TOKEN_STORAGE_PREFIX}${json.id}`, json.access_token);
  return {
    player: { id: json.id, preferences: json.preferences },
    issuedToken: true,
  };
}

/** Create a named identity before mounting any player-scoped screen. */
export async function createActivePlayer(displayName: string): Promise<string> {
  const name = displayName.trim();
  if (!name) throw new Error('Enter a display name');
  const id = crypto.randomUUID();
  const registration = await registerPlayer({ id, display_name: name });
  if (!registration.issuedToken) throw new Error('Could not create a new player identity');
  setActivePlayerId(id);
  return id;
}

export async function ensurePlayerAuthorization(playerId: string): Promise<string> {
  if (localStorage.getItem(`${PLAYER_TOKEN_STORAGE_PREFIX}${playerId}`) !== null) return playerId;
  const existing = pendingPlayerAuthorizations.get(playerId);
  if (existing !== undefined) return await existing;

  const request = (async (): Promise<string> => {
    const registration = await registerPlayer({ id: playerId, display_name: 'Anonymous' });
    if (registration.issuedToken) return playerId;

    const freshPlayerId = crypto.randomUUID();
    const freshRegistration = await registerPlayer({ id: freshPlayerId, display_name: 'Anonymous' });
    if (!freshRegistration.issuedToken) {
      throw new Error(`Could not authorize a new player identity for ${playerId}`);
    }
    setActivePlayerId(freshPlayerId);
    return freshPlayerId;
  })();
  pendingPlayerAuthorizations.set(playerId, request);
  try {
    return await request;
  } finally {
    pendingPlayerAuthorizations.delete(playerId);
  }
}

/**
 * Record the identity the player is actually playing under so the profile,
 * training, and coach screens read the same player as the play screen.
 */
export function setActivePlayerId(playerId: string): void {
  localStorage.setItem(PLAYER_ID_STORAGE_KEY, playerId);
}


export function getPlayerAuthorizationHeader(playerId: string): Record<string, string> {
  const token = localStorage.getItem(`${PLAYER_TOKEN_STORAGE_PREFIX}${playerId}`);
  if (!token) {
    throw new Error(`No access token is stored for player ${playerId}`);
  }

  return { Authorization: `Bearer ${token}` };
}

function getActivePlayerAuthorizationHeader(): Record<string, string> {
  try {
    return getPlayerAuthorizationHeader(getActivePlayerId());
  } catch {
    throw new ApiRequestError('Player authorization is required', 401);
  }
}

export class ApiClient {
  static async getAppInfo(): Promise<{ distribution: string }> {
    const response = await fetch(`${API_BASE}/app/info`);
    if (!response.ok) throw new Error('Application information unavailable');
    return response.json() as Promise<{ distribution: string }>;
  }

  static async getAppUpdate(): Promise<{ status: string; version: string | null }> {
    const response = await fetch(`${API_BASE}/app/update`, { cache: 'no-store' });
    if (!response.ok) throw new Error('Application update information unavailable');
    return response.json() as Promise<{ status: string; version: string | null }>;
  }

  private static async puzzleRequest<T>(path: string, method: 'GET' | 'POST', data?: object): Promise<T> {
    const response = await fetch(`${API_BASE}/puzzles${path}`, {
      method,
      headers: { ...getActivePlayerAuthorizationHeader(), ...(data ? { 'Content-Type': 'application/json' } : {}) },
      ...(data ? { body: JSON.stringify(data) } : {}),
    });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = body !== null && typeof body === 'object' && 'detail' in body ? body.detail : null;
      if (response.status === 409 && detail !== null && typeof detail === 'object'
        && 'message' in detail && typeof detail.message === 'string' && 'run' in detail
        && detail.run !== null && typeof detail.run === 'object' && 'run_id' in detail.run) {
        throw new PuzzleConflictError(detail.message, detail.run as PuzzleRunRead);
      }
      throw new ApiRequestError(typeof detail === 'string' ? detail : `Puzzle request failed: ${response.statusText}`, response.status);
    }
    return body as T;
  }

  static getPuzzleDaily(): Promise<PuzzleDailyRead> {
    return this.puzzleRequest('/daily', 'GET');
  }

  static getActivePuzzleRun(): Promise<PuzzleStartRead> {
    return this.puzzleRequest('/active', 'GET');
  }

  static startPuzzleRun(body?: PuzzleRunCreate): Promise<PuzzleStartRead> {
    return this.puzzleRequest('/runs', 'POST', body);
  }

  static getPuzzleModes(): Promise<PuzzleModesRead> {
    return this.puzzleRequest('/modes', 'GET');
  }

  static getPuzzleMistakes(offset = 0, limit = 20): Promise<PuzzleMistakesRead> {
    return this.puzzleRequest(`/mistakes?offset=${offset}&limit=${limit}`, 'GET');
  }

  static startPuzzleMistake(lessonId: string): Promise<PuzzleMistakeStartRead> {
    return this.puzzleRequest(`/mistakes/${encodeURIComponent(lessonId)}/start`, 'POST');
  }

  static getPuzzleRun(runId: string): Promise<PuzzleRunRead> {
    return this.puzzleRequest(`/runs/${encodeURIComponent(runId)}`, 'GET');
  }

  static submitPuzzleMove(runId: string, body: PuzzleExpectedState & { move: string }): Promise<PuzzleRunRead> {
    return this.puzzleRequest(`/runs/${encodeURIComponent(runId)}/moves`, 'POST', body);
  }

  static revealPuzzleHint(runId: string, body: PuzzleExpectedState & { step: 1 | 2 | 3 }): Promise<PuzzleRunRead> {
    return this.puzzleRequest(`/runs/${encodeURIComponent(runId)}/hints`, 'POST', body);
  }

  static abandonPuzzleRun(runId: string, body: PuzzleExpectedState): Promise<PuzzleRunRead> {
    return this.puzzleRequest(`/runs/${encodeURIComponent(runId)}/abandon`, 'POST', body);
  }

  static async getPositionInsight(data: PositionInsightRequest): Promise<PositionInsight> {
    const response = await fetch(`${API_BASE}/insights/position`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getPlayerAuthorizationHeader(data.player_id) },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      const detail = body !== null && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail : response.statusText;
      throw new ApiRequestError(`Failed to get hint: ${detail}`, response.status);
    }
    return await response.json() as PositionInsight;
  }

  static async createGame(data: GameCreate): Promise<GameRead> {
    const response = await fetch(`${API_BASE}/games`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getPlayerAuthorizationHeader(data.player_id),
      },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create game: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as GameRead;
  }

  static async getGame(gameId: string): Promise<GameRead> {
    const response = await fetch(`${API_BASE}/games/${gameId}`, {
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get game: ${response.statusText}`, response.status);
    }
    return await response.json() as GameRead;
  }

  static async getPlayerGames(playerId: string, cursor?: string): Promise<PlayerGamesPage> {
    const query = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
    const response = await fetch(
      `${API_BASE}/players/${encodeURIComponent(playerId)}/games${query}`,
      { headers: getPlayerAuthorizationHeader(playerId) },
    );
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get player games: ${response.statusText}`, response.status);
    }
    return await response.json() as PlayerGamesPage;
  }

  static async getPositions(gameId: string): Promise<PositionRead[]> {
    const response = await fetch(`${API_BASE}/games/${gameId}/positions`, {
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get positions: ${response.statusText}`, response.status);
    }
    const json = await response.json();
    return json as unknown as PositionRead[];
  }

  static async getGameAnalysisStatus(gameId: string): Promise<GameAnalysisStatusRead> {
    const response = await fetch(`${API_BASE}/games/${gameId}/analysis-status`, {
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      throw new ApiRequestError(
        `Failed to get game analysis status: ${response.statusText}`,
        response.status,
      );
    }
    return await response.json() as GameAnalysisStatusRead;
  }

  static async getGameReview(gameId: string): Promise<GameReviewRead> {
    const response = await fetch(`${API_BASE}/games/${encodeURIComponent(gameId)}/review`, {
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get game review: ${response.statusText}`, response.status);
    }
    return await response.json() as GameReviewRead;
  }

  static async backfillGameReview(gameId: string): Promise<GameReviewRead> {
    const response = await fetch(`${API_BASE}/games/${encodeURIComponent(gameId)}/review/backfill`, {
      method: 'POST',
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      const detail = body !== null && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail : response.statusText;
      throw new ApiRequestError(`Failed to backfill game review: ${detail}`, response.status);
    }
    return await response.json() as GameReviewRead;
  }

  static async getLearningOpportunities(gameId: string): Promise<GameLearningSessionRead> {
    let playerId = getOrCreatePlayerId();
    playerId = await ensurePlayerAuthorization(playerId);
    const response = await fetch(
      `${API_BASE}/games/${gameId}/learning-opportunities?player_id=${encodeURIComponent(playerId)}`,
      { headers: getPlayerAuthorizationHeader(playerId) },
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch learning opportunities: ${response.statusText}`);
    }
    return await response.json() as GameLearningSessionRead;
  }

  static async createAnalysisJob(gameId: string): Promise<AnalysisJobRead> {
    const response = await fetch(`${API_BASE}/games/${gameId}/analysis-jobs`, {
      method: 'POST',
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      const error: unknown = await response.json().catch(() => null);
      const detail = error !== null && typeof error === 'object' && 'detail' in error && typeof error.detail === 'string'
        ? error.detail : response.statusText;
      throw new ApiRequestError(`Failed to create analysis job: ${detail}`, response.status);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
  }

  static async getAnalysisJob(jobId: string): Promise<AnalysisJobRead> {
    const response = await fetch(`${API_BASE}/analysis-jobs/${jobId}`, {
      headers: getActivePlayerAuthorizationHeader(),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get analysis job: ${response.statusText}`, response.status);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
  }

  static async getBots(): Promise<BotRead[]> {
    const response = await fetch(`${API_BASE}/bots`);
    if (!response.ok) throw new ApiRequestError(`Failed to fetch bots: ${response.statusText}`, response.status);
    return await response.json() as BotRead[];
  }

  static async takebackPlaySession(sessionId: string): Promise<PlaySessionRead> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}/takeback`, {
      method: 'POST',
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
    });
    if (!response.ok) throw new ApiRequestError(`Failed to take back: ${response.statusText}`, response.status);
    return await response.json() as PlaySessionRead;
  }

  static async createPlaySession(data: PlaySessionCreate): Promise<PlaySessionRead> {
    const response = await fetch(`${API_BASE}/play-sessions`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getPlayerAuthorizationHeader(data.player_id),
      },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create play session: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlaySessionRead;
  }

  static async getPlaySession(sessionId: string): Promise<PlaySessionRead> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}`, {
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to get play session: ${response.statusText}`, response.status);
    }
    return await response.json() as PlaySessionRead;
  }

  static async makePlaySessionMove(sessionId: string, move: PlayMoveCreate): Promise<PlayMoveResponse> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}/moves`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getPlayerAuthorizationHeader(getActivePlayerId()),
      },
      body: JSON.stringify(move),
    });
    if (!response.ok) {
      throw new Error(`Failed to make move: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlayMoveResponse;
  }

  static async resignPlaySession(sessionId: string): Promise<PlaySessionRead> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}/resign`, {
      method: 'POST',
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Failed to resign: ${response.statusText}`, response.status);
    }
    return await response.json() as PlaySessionRead;
  }

  static async createPlayer(data: PlayerCreate): Promise<PlayerRead> {
    return (await registerPlayer(data)).player;
  }

  static async getFamousGames(): Promise<FamousGameRead[]> {
    const response = await fetch(`${API_BASE}/content/famous-games`);
    if (!response.ok) {
      throw new Error(`Failed to fetch famous games: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as FamousGameRead[];
  }

  static async getFamousGame(id: string): Promise<FamousGameRead> {
    const response = await fetch(`${API_BASE}/content/famous-games/${id}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch famous game: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as FamousGameRead;
  }

  static async recordFamousGameAttempt(id: string, attempt: AttemptCreate): Promise<AttemptRead> {
    const response = await fetch(`${API_BASE}/content/famous-games/${id}/attempts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getPlayerAuthorizationHeader(attempt.player_id),
      },
      body: JSON.stringify(attempt),
    });
    if (!response.ok) {
      throw new Error(`Failed to record attempt: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as AttemptRead;
  }

  static async getTrainingSession(): Promise<TrainingSessionRead> {
    let playerId = getOrCreatePlayerId();
    playerId = await ensurePlayerAuthorization(playerId);
    const response = await fetch(`${API_BASE}/learning/session?player_id=${playerId}`, {
      headers: getPlayerAuthorizationHeader(playerId),
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch training session: ${response.statusText}`);
    }
    return await response.json() as TrainingSessionRead;
  }

  static async recordLessonAttempt(attempt: LessonAttemptCreate): Promise<LessonAttemptRead> {
    const response = await fetch(`${API_BASE}/learning/lesson-attempts`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...getPlayerAuthorizationHeader(getOrCreatePlayerId()),
      },
      body: JSON.stringify(attempt),
    });
    if (!response.ok) {
      throw new Error(`Failed to record lesson attempt: ${response.statusText}`);
    }
    return await response.json() as LessonAttemptRead;
  }

  private static async getPlayerResource<T>(
    playerId: string,
    resource: string,
  ): Promise<T> {
    const response = await fetch(`${API_BASE}/players/${playerId}/${resource}`, {
      headers: getPlayerAuthorizationHeader(playerId),
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch player ${resource}: ${response.statusText}`);
    }
    return await response.json() as T;
  }

  static async getPlayerProfile(playerId: string): Promise<PlayerProfileRead> {
    return this.getPlayerResource<PlayerProfileRead>(playerId, 'profile');
  }

  static getPlayerOnboarding(playerId: string): Promise<PlayerOnboardingRead> {
    return this.getPlayerResource<PlayerOnboardingRead>(playerId, 'onboarding');
  }

  static getPlayerStrength(playerId: string): Promise<StrengthRead> {
    return this.getPlayerResource<StrengthRead>(playerId, 'strength');
  }

  private static async habitRequest(
    playerId: string,
    path: string,
    method: 'GET' | 'POST',
    data?: { goal: number } | { code: string } | Record<string, never>,
  ): Promise<HabitSummaryRead> {
    const response = await fetch(`${API_BASE}/players/${encodeURIComponent(playerId)}/habit${path}`, {
      method,
      headers: { ...getPlayerAuthorizationHeader(playerId), ...(data ? { 'Content-Type': 'application/json' } : {}) },
      ...(data ? { body: JSON.stringify(data) } : {}),
    });
    if (!response.ok) {
      const body: unknown = await response.json().catch(() => null);
      const detail = body !== null && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string'
        ? body.detail : response.statusText;
      throw new ApiRequestError(`Habit request failed: ${detail}`, response.status);
    }
    return await response.json() as HabitSummaryRead;
  }

  static getPlayerHabit(playerId: string): Promise<HabitSummaryRead> {
    return this.habitRequest(playerId, '', 'GET');
  }

  static updateHabitGoal(playerId: string, goal: number): Promise<HabitSummaryRead> {
    return this.habitRequest(playerId, '/goal', 'POST', { goal });
  }

  static startHabitTarget(playerId: string, code: string): Promise<HabitSummaryRead> {
    return this.habitRequest(playerId, '/targets', 'POST', { code });
  }

  static refreshHabitTarget(playerId: string, targetId: string): Promise<HabitSummaryRead> {
    return this.habitRequest(playerId, `/targets/${encodeURIComponent(targetId)}/refresh`, 'POST', {});
  }

  private static async updatePlayerChoice(
    playerId: string,
    resource: 'onboarding' | 'bot-choice',
    method: 'POST' | 'PUT',
    data: OnboardingAction | { bot_level: number },
  ): Promise<PlayerOnboardingRead> {
    const response = await fetch(`${API_BASE}/players/${encodeURIComponent(playerId)}/${resource}`, {
      method,
      headers: { 'Content-Type': 'application/json', ...getPlayerAuthorizationHeader(playerId) },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new ApiRequestError(`Could not save ${resource}: ${response.statusText}`, response.status);
    }
    return await response.json() as PlayerOnboardingRead;
  }

  static updatePlayerOnboarding(playerId: string, data: OnboardingAction): Promise<PlayerOnboardingRead> {
    return this.updatePlayerChoice(playerId, 'onboarding', 'POST', data);
  }

  static setPlayerBotChoice(playerId: string, botLevel: number): Promise<PlayerOnboardingRead> {
    return this.updatePlayerChoice(playerId, 'bot-choice', 'PUT', { bot_level: botLevel });
  }

  static async getPlayerProgress(playerId: string): Promise<PlayerProgressReport> {
    return this.getPlayerResource<PlayerProgressReport>(playerId, 'progress');
  }

  static async getWeeklyReport(playerId: string): Promise<WeeklyReport> {
    const response = await fetch(
      `${API_BASE}/reports/weekly?player_id=${encodeURIComponent(playerId)}`,
      { headers: getPlayerAuthorizationHeader(playerId) },
    );
    if (!response.ok) {
      throw new Error(`Failed to fetch weekly report: ${response.statusText}`);
    }
    return await response.json() as WeeklyReport;
  }

  static async getPlayerPatterns(playerId: string): Promise<PatternsReport> {
    return this.getPlayerResource<PatternsReport>(playerId, 'patterns');
  }

  static async getPlayerEvidence(playerId: string): Promise<EvidenceReport> {
    return this.getPlayerResource<EvidenceReport>(playerId, 'evidence');
  }

  static async getCoachDashboard(coachId: string): Promise<CoachDashboard> {
    const response = await fetch(`${API_BASE}/coaches/${coachId}/dashboard`, {
      headers: getPlayerAuthorizationHeader(coachId),
    });
    if (!response.ok) {
      throw new Error(`Failed to fetch coach dashboard: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as CoachDashboard;
  }
}
