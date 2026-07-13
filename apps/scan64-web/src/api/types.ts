export interface Visualization {
  command: string;
  description: string;
  squares?: string[];
  moves?: string[];
}

export interface Hint {
  level: number;
  kind: string;
  text: string;
  squares?: string[];
  visualizations?: Visualization[];
}

export interface AcceptedMove {
  san: string;
  lan: string;
  reason: string;
}

export interface Interaction {
  input: string;
  maximum_attempts: number;
  accepted_moves: AcceptedMove[];
}

export interface Diagnosis {
  primary: string;
  secondary: string[];
  confidence: number;
  evidence_refs: string[];
}

export interface Source {
  kind: string;
  fen: string;
}

export interface Objective {
  type: string;
  instruction: string;
}

export interface Explanation {
  text: string;
  visualizations?: Visualization[];
}

export interface Verification {
  status: string;
  engine: string;
  engine_binary_digest: string;
  nodes: number;
  multipv: number;
  verified_at: string;
}

export interface MasteryImpact {
  skill_key: string;
  delta: number;
}

export interface LessonSpec {
  schema_version: string;
  lesson_id: string;
  source: Source;
  diagnosis: Diagnosis;
  objective: Objective;
  interaction: Interaction;
  hints: Hint[];
  explanation: Explanation;
  verification: Verification;
  mastery: MasteryImpact;
}

export interface GameCreate {
  pgn: string;
}

export interface GameRead {
  id: string;
  pgn: string;
  white: string;
  black: string;
  result: string;
}

export interface PlaySessionMove {
  lan: string;
}

export interface AnalysisJobRead {
  id: string;
  game_id: string;
  status: string;
}


export interface PlaySession {
  id: string;
  fen: string;
  pgn: string;
  status: string;
}

export interface PlayerCreate {
  id: string;
  display_name?: string;
  preferences?: Record<string, unknown>;
}

export interface PlayerRead {
  id: string;
  preferences: Record<string, unknown>;
}


export interface PlaySessionCreate {
  player_id: string;
  game_id?: string;
  opponent_config?: Record<string, string>;
  clock_config?: Record<string, string>;
}

export interface PlaySessionRead {
  id: string;
  player_id: string;
  game_id?: string;
  opponent_config: Record<string, string>;
  clock_config?: Record<string, string>;
  status: string;
}

export interface PlayMoveCreate {
  move: string;
}

export interface PlayMoveResponse {
  opponent_move: string | null;
  interruption_lesson?: LessonSpec | null;
}

export interface EngineAnalysisRead {
  id: string;
  config: Record<string, unknown>;
  raw_result: Array<{
    pv: string[];
    score_cp?: number;
    score_mate?: number;
  }>;
}

export interface PositionRead {
  id: string;
  fen: string;
  half_move_clock: number;
  full_move_number: number;
  side_to_move: string;
  canonical_id: string;
  analysis?: EngineAnalysisRead;
}
