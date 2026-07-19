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
  initial_fen?: string;
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

export interface VerifiedAlternative {
  san: string;
  explanation: string;
}

export interface FamousGameDecision {
  id: string;
  ply: number;
  fen: string;
  prompt: string;
  played_move: string;
  accepted_moves: string[];
  verified_alternatives: VerifiedAlternative[];
  hints: string[];
  comparison: string;
}

export interface FamousGamePayload {
  title: string;
  historical_context: string;
  strategic_context: string;
  moves: string[];
  decisions: FamousGameDecision[];
}

export interface FamousGameRead {
  id: string;
  payload: FamousGamePayload;
  skill_mapping: Record<string, number>;
}

export interface AttemptCreate {
  player_id: string;
  decision_id: string;
  hint_assisted: boolean;
  response_payload: Record<string, unknown>;
}

export interface AttemptRead {
  id: string;
  success: boolean;
  hint_assisted: boolean;
}

export interface PlayerProfileRead {
  player_id: string;
  rating: number;
  display_name: string | null;
}

export interface PatternRead {
  description?: string;
  rule_id?: string;
}

export interface PatternsReport {
  player_id: string;
  recurring_habits: PatternRead[];
}

export interface EvidenceItemRead {
  evidence_id: string;
  kind: string;
  position_id: string;
  claim: string;
  payload: Record<string, unknown>;
  producer: Record<string, unknown>;
}

export interface EvidenceReport {
  player_id: string;
  evidence_items: EvidenceItemRead[];
}

export interface CoachStudentDashboard {
  student_id: string;
  profile: PlayerProfileRead;
  patterns: PatternsReport;
  evidence: EvidenceReport;
}

export interface CoachDashboard {
  coach_id: string;
  students: CoachStudentDashboard[];
}

export interface ProgressSkillRead {
  concept: string;
  mastery: number;
  uncertainty: number;
}

export interface PlayerProgressReport {
  player_id: string;
  skills: ProgressSkillRead[];
}
