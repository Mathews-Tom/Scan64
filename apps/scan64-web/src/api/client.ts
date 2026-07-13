import { GameCreate, GameRead, LessonSpec, PlaySession, PlaySessionMove } from './types';

const API_BASE = '/api/v1';

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

  static async getLearningOpportunities(gameId: string): Promise<LessonSpec[]> {
    const response = await fetch(`${API_BASE}/games/${gameId}/learning-opportunities`);
    if (!response.ok) {
      throw new Error(`Failed to get learning opportunities: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as LessonSpec[];
  }

  static async createPlaySession(opponentStrength: string): Promise<PlaySession> {
    const response = await fetch(`${API_BASE}/play-sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ opponent: opponentStrength }),
    });
    if (!response.ok) {
      throw new Error(`Failed to create play session: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlaySession;
  }

  static async makePlaySessionMove(sessionId: string, move: PlaySessionMove): Promise<PlaySession> {
    const response = await fetch(`${API_BASE}/play-sessions/${sessionId}/moves`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(move),
    });
    if (!response.ok) {
      throw new Error(`Failed to make move: ${response.statusText}`);
    }
    const json = await response.json();
    return json as unknown as PlaySession;
  }
}
