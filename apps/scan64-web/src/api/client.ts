import type {
  AnalysisJobRead,
  AttemptCreate,
  AttemptRead,
  CoachDashboard,
  EvidenceReport,
  FamousGameRead,
  GameCreate,
  GameRead,
  LessonSpec,
  PlayMoveCreate,
  PlayMoveResponse,
  PlayerCreate,
  PlayerRead,
  PatternsReport,
  PlayerProfileRead,
  PlayerProgressReport,
  PlaySessionCreate,
  PlaySessionRead,
  PositionRead,
} from './types';

const API_BASE = '/v1';
const PLAYER_TOKEN_STORAGE_PREFIX = 'scan64_player_token:';

export function getOrCreatePlayerId(): string {
  const existingPlayerId = localStorage.getItem('scan64_player_id');
  if (existingPlayerId) return existingPlayerId;

  const playerId = crypto.randomUUID();
  localStorage.setItem('scan64_player_id', playerId);
  return playerId;
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
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create game: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as GameRead;
  }

  static async getPositions(gameId: string): Promise<PositionRead[]> {
    const response = await fetch(`${API_BASE}/games/${gameId}/positions`);
    if (!response.ok) {
      if (response.status === 404) return [];
      throw new Error(`Failed to get positions: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PositionRead[];
  }

  static async getLearningOpportunities(gameId: string): Promise<LessonSpec[]> {
    const response = await fetch(`${API_BASE}/games/${gameId}/learning-opportunities`);
    if (!response.ok) {
      throw new Error(`Failed to get learning opportunities: ${response.statusText}`);
    }
    const json = await response.json();
    if (json && typeof json === 'object' && 'items' in json && Array.isArray(json.items)) {
      return json.items as LessonSpec[];
    }
    return [];
  }

  static async createAnalysisJob(gameId: string): Promise<AnalysisJobRead> {
    const response = await fetch(`${API_BASE}/games/${gameId}/analysis-jobs`, {
      method: 'POST',
    });
    if (!response.ok) {
      throw new Error(`Failed to create analysis job: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
  }

  static async getAnalysisJob(jobId: string): Promise<AnalysisJobRead> {
    const response = await fetch(`${API_BASE}/analysis-jobs/${jobId}`);
    if (!response.ok) {
      throw new Error(`Failed to get analysis job: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as AnalysisJobRead;
  }

  static async createPlaySession(data: PlaySessionCreate): Promise<PlaySessionRead> {
    const response = await fetch(`${API_BASE}/play-sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create play session: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlaySessionRead;
  }

  static async makePlaySessionMove(sessionId: string, move: PlayMoveCreate): Promise<PlayMoveResponse> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}/moves`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(move),
    });
    if (!response.ok) {
      throw new Error(`Failed to make move: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlayMoveResponse;
  }

  static async createPlayer(data: PlayerCreate): Promise<PlayerRead> {
    const response = await fetch(`${API_BASE}/players`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data),
    });
    if (!response.ok) {
      throw new Error(`Failed to create player: ${response.statusText}`);
    }
    const json = await response.json() as PlayerRead & { access_token?: unknown };
    if (typeof json.access_token !== 'string') {
      throw new Error('Player creation response did not include an access token');
    }
    localStorage.setItem(`${PLAYER_TOKEN_STORAGE_PREFIX}${json.id}`, json.access_token);
    return { id: json.id, preferences: json.preferences };
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

  static async getTrainingSession(): Promise<LessonSpec[]> {
    const playerId = getOrCreatePlayerId();
    const response = await fetch(`${API_BASE}/learning/session?player_id=${playerId}`);
    if (!response.ok) {
      throw new Error(`Failed to fetch training session: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as LessonSpec[];
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
