export interface Visualization {
  command: string;
  description: string;
  origin?: string;
  target?: string;
  square?: string;
  label?: string;
  squares?: string[];
  moves?: string[];
}

export type PositionInsightRequest = { player_id: string; step: 1 | 2 | 3 } & (
  { fen: string; play_session_id?: never } | { play_session_id: string; fen?: never }
);

export interface MoveInsight {
  step: 3;
  fen: string;
  candidates: {
    number: number;
    move: string;
    san: string;
    line: string[];
    reasons: { kind: string; tier: 'tactical' | 'principle'; description: string; line: string[]; squares: string[] }[];
    fallback: boolean;
    visualizations: Visualization[];
  }[];
  visualizations: Visualization[];
}

export type PositionInsight = MoveInsight | {
  step: 1;
  text: string;
  visualizations: Visualization[];
} | {
  step: 2;
  text: string;
  motif: string;
  piece: string;
  square: string;
  targets: string[];
  visualizations: Visualization[];
};

/** The server's three-step hint union, shared with position hints. */
export type InsightStep = PositionInsight;

export type DeclaredLevel = 'beginner' | 'developing' | 'club' | 'advanced' | 'not_sure';

export interface PlayerOnboardingRead {
  player_id: string;
  status: 'pending' | 'not_offered' | 'in_progress' | 'skipped' | 'completed';
  declared_level: DeclaredLevel | null;
  suggested_bot_level: number | null;
  mapping_version: 'declared-level-v1' | null;
  intro_started_at: string | null;
  skipped_at: string | null;
  completed_at: string | null;
  intro_started: number;
  intro_closed: number;
  intro_target: number;
  preferred_bot_level: number;
  manual_bot_level: number | null;
}

export interface OnboardingAction {
  action: 'declare' | 'skip' | 'continue_later';
  declared_level?: DeclaredLevel;
}

export interface BotResultsSample {
  sample_size: number;
  wins: number;
  draws: number;
  losses: number;
}

export interface StrengthRead {
  declared_level: DeclaredLevel | null;
  suggested_bot_level: number | null;
  puzzle_rating: number;
  puzzle_rated_count: number;
  puzzle_provisional: boolean;
  puzzle_events: { sequence: number; before: number; after: number; score: number }[];
  bot_cohorts: (BotResultsSample & {
    provider: 'stockfish';
    bot_level: number;
    bot_policy_version: string;
    latest: BotResultsSample;
    previous: BotResultsSample;
    trend: 'improving' | 'steady' | 'declining' | 'insufficient_data';
  })[];
  disclosure: string;
}

export interface PuzzleDailyRead {
  utc_day: string;
  rating: number;
  rated_count: number;
  provisional: boolean;
  overdue_count: number;
  total: number;
  completed: number;
  remaining: number;
  actual_mix: { due: number; weak: number; exploration: number };
  message: string;
}

export type PuzzleMode = 'daily' | 'theme' | 'streak';
export interface PuzzleStreakRead {
  current: number;
  best: number;
  reset_reason: string | null;
}
export interface PuzzleModesRead {
  themes: string[];
  streak: PuzzleStreakRead;
}
export interface PuzzleRunCreate {
  mode: PuzzleMode;
  theme?: string;
}
export interface PuzzleMistakesRead {
  items: { lesson_id: string; game_id: string; ply: number; attempts_remaining: number }[];
  excluded: { no_review_schedule: number; retired: number; untrusted: number; exhausted: number };
  offset: number;
  limit: number;
  has_more: boolean;
}
export interface PuzzleMistakeStartRead {
  moment: GameReviewKeyMoment;
  session_id: string;
}

export interface PuzzleRunRead {
  run_id: string;
  puzzle_id: string;
  fen: string;
  setup: string;
  played_moves: string[];
  cursor: number;
  revision: number;
  wrong_attempts: number;
  maximum_wrong_attempts: number;
  state: 'active' | 'solved' | 'failed' | 'abandoned';
  hint_step: number;
  hints: InsightStep[];
  selection_reason: string | null;
  widening: string | null;
  rated: boolean;
  daily: PuzzleDailyRead;
  mode: PuzzleMode;
  theme: string | null;
  streak: PuzzleStreakRead;
}

export interface PuzzleStartRead {
  status: 'idle' | 'ready' | 'empty' | 'exhausted';
  run: PuzzleRunRead | null;
  message: string;
  daily: PuzzleDailyRead;
  streak: PuzzleStreakRead;
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
  lan?: string | null;
  reason?: string | null;
}

export interface Interaction {
  input: string;
  maximum_attempts: number;
  accepted_moves: AcceptedMove[];
  solution?: string[];
}

export interface Diagnosis {
  primary: string;
  secondary: string[];
  confidence: number;
  evidence_refs: string[];
}

export interface Source {
  kind: 'player_game' | 'custom' | 'external';
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
  engine?: string | null;
  engine_binary_digest?: string | null;
  nodes?: number | null;
  multipv?: number | null;
  verified_at?: string | null;
}

export interface MasteryImpact {
  skill_key: string;
  delta: number;
}

export type LessonSpec = {
  schema_version: string;
  lesson_id: string;
  objective: Objective;
  interaction: Interaction;
  hints: Hint[];
  explanation?: Explanation | null;
  verification: Verification;
  mastery?: MasteryImpact | null;
} & (
  | { source: Source & { kind: 'player_game' }; diagnosis: Diagnosis }
  | { source: Source & { kind: 'custom' | 'external' }; diagnosis?: Diagnosis | null }
);

export interface TrainingSessionRead {
  session_id: string;
  lessons: LessonSpec[];
}

export interface GameLearningSessionRead {
  session_id: string | null;
  lessons: LessonSpec[];
  next_cursor: string | null;
}

export type ReviewBand = 'sound' | 'inaccuracy' | 'mistake' | 'blunder' | 'unscored';

export interface GameMoveReviewRead {
  ply: number;
  source_fen: string;
  fen: string;
  move_uci: string;
  san: string;
  mover: 'white' | 'black';
  before_probability: number | null;
  after_probability: number | null;
  white_probability: number | null;
  loss: number | null;
  band: ReviewBand | null;
  is_owner_move: boolean;
  credit?: Record<string, unknown> | null;
  credit_exclusion?: string | null;
}

export interface GameReviewKeyMoment {
  ply: number;
  lesson_id: string;
  lesson: LessonSpec;
  attempts_used: number;
  attempts_remaining: number;
}

export interface GameReviewRead {
  game_id: string;
  owner_color: 'white' | 'black' | null;
  initial_fen: string;
  initial_white_probability: number | null;
  moves: GameMoveReviewRead[];
  accuracy: number | null;
  scored_moves: number;
  total_owner_moves: number;
  band_counts: Record<ReviewBand, number>;
  key_moments: GameReviewKeyMoment[];
  session_id: string | null;
  backfill_required: boolean;
  nodes: 10000;
}

export interface LessonAttemptCreate {
  session_id: string;
  lesson_id: string;
  source_kind: 'persisted_opportunity' | 'opening_mission' | 'transfer_measurement';
  submitted_move?: string;
  elapsed_ms: number;
  hints_used: number;
}

export interface LessonAttemptRead {
  id: string;
  success: boolean | null;
  grading_status: string;
  profile_update_result: string;
}

export interface GameCreate {
  owner_color: 'white' | 'black';
  pgn: string;
  player_id: string;
}

export interface GameRead {
  id: string;
  pgn: string;
  white: string;
  black: string;
  result: string;
}

export interface PlayerGameRead {
  id: string;
  white: string;
  black: string;
  result: string;
  date: string;
  created_at: string;
  diagnosis_count: number;
}

export interface PlayerGamesPage {
  items: PlayerGameRead[];
  next_cursor: string | null;
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


export interface BotRead {
  level: number;
  name: string;
  description: string;
}

export interface PlaySessionCreate {
  player_color?: 'white' | 'black' | 'random';
  player_id: string;
  game_id?: string;
  opponent_config?: Record<string, string>;
  clock_config?: Record<string, string>;
  initial_fen?: string;
  coach_mode?: boolean;
}

export interface PlaySessionRead {
  player_color?: 'white' | 'black';
  opponent_move?: string | null;
  id: string;
  player_id: string;
  game_id?: string;
  opponent_config: Record<string, string>;
  clock_config?: Record<string, string>;
  status: string;
  coach_mode: boolean;
  assistance_tracking_version?: number | null;
  credit_analysis_status?: string | null;
}

export interface PlayMoveCreate {
  move: string;
}


export interface CriticalInterruptionRead {
  lesson: LessonSpec;
  opportunity_id: string;
  study_session_id: string;
}

export interface PlayMoveResponse {
  opponent_move: string | null;
  status: string;
  critical_interruption?: CriticalInterruptionRead | null;
}

export interface DiagnosisRead {
  primary: string;
  secondary: string[];
  confidence: number;
}

export interface GameAnalysisStatusRead {
  status: string;
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
  diagnoses: DiagnosisRead[];
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

export interface DiagnosisPatternRead {
  diagnosis: string;
  name: string;
  occurrence_count: number;
  game_ids: string[];
  evidence_references: string[];
}

export type PatternReportStatus =
  | 'insufficient_data'
  | 'no_recurring_diagnosis'
  | 'recurring_diagnosis';

export interface PatternsReport {
  player_id: string;
  minimum_occurrences: number;
  status: PatternReportStatus;
  recurring_diagnoses: DiagnosisPatternRead[];
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
  strength: StrengthRead;
  patterns: PatternsReport;
  evidence: EvidenceReport;
}

export interface CoachDashboard {
  coach_id: string;
  students: CoachStudentDashboard[];
}

export interface ProgressSkillRead {
  concept: string;
  name: string;
  mastery: number;
  uncertainty: number;
}

export interface PlayerProgressReport {
  player_id: string;
  skills: ProgressSkillRead[];
}

export interface WeeklyReport {
  player_id: string;
  games_played: number;
  active_concepts_observed: number;
  active_mastery: Array<{ concept_code: string; name: string; mastery: number }>;
  top_recurring_diagnosis: DiagnosisPatternRead | null;
}
