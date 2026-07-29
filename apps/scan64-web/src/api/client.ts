import type {
  AnalysisJobRead,
  AttemptCreate,
  AttemptRead,
  CoachDashboard,
  EvidenceReport,
  FamousGameRead,
  GameCreate,
  GameRead,
  GameLearningSessionRead,
  LessonAttemptCreate,
  LessonAttemptRead,
  PlayMoveCreate,
  PlayMoveResponse,
  PlayerCreate,
  PlayerRead,
  PlayerGamesPage,
  PatternsReport,
  PlayerProfileRead,
  PlayerProgressReport,
  PlaySessionCreate,
  PlaySessionRead,
  PositionRead,
  TrainingSessionRead,
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

interface PlayerRegistration {
  player: PlayerRead;
  issuedToken: boolean;
}

export function getActivePlayerId(): string {
  const playerId = localStorage.getItem(PLAYER_ID_STORAGE_KEY);
  if (!playerId) throw new Error('No active player identity is stored');
  return playerId;
}

export function getOrCreatePlayerId(): string {
  const existingPlayerId = localStorage.getItem(PLAYER_ID_STORAGE_KEY);
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

export class ApiClient {
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
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
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
    let playerId = getOrCreatePlayerId();
    playerId = await ensurePlayerAuthorization(playerId);
    const response = await fetch(`${API_BASE}/games/${gameId}/positions`, {
      headers: getPlayerAuthorizationHeader(playerId),
    });
    if (!response.ok) {
      if (response.status === 404) return [];
      throw new Error(`Failed to get positions: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PositionRead[];
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
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
    });
    if (!response.ok) {
      throw new Error(`Failed to create analysis job: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
  }

  static async getAnalysisJob(jobId: string): Promise<AnalysisJobRead> {
    const response = await fetch(`${API_BASE}/analysis-jobs/${jobId}`, {
      headers: getPlayerAuthorizationHeader(getActivePlayerId()),
    });
    if (!response.ok) {
      throw new Error(`Failed to get analysis job: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
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
      headers: { 'Content-Type': 'application/json' },
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

  static async getPlayerProgress(playerId: string): Promise<PlayerProgressReport> {
    return this.getPlayerResource<PlayerProgressReport>(playerId, 'progress');
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
