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

export interface PositionAnalysisRequest {
  player_id: string;
  fen: string;
}

export interface PositionAnalysisMove {
  uci: string;
  san: string;
}

export interface PositionAnalysisLine {
  score_cp: number | null;
  mate: number | null;
  moves: PositionAnalysisMove[];
}

export interface PositionAnalysisRead {
  fen: string;
  lines: PositionAnalysisLine[];
}

/** Analysis Board line explanation request; budgets may only narrow the analysis limits. */
export interface PositionExplanationRequest {
  player_id: string;
  fen: string;
  multipv?: number;
  nodes?: number;
  depth?: number;
}

export interface PositionExplanationLine extends PositionAnalysisLine {
  line_id: string;
  rank: number;
  depth: number | null;
  seldepth: number | null;
}

/** One grounded claim; `moves` is the UCI prefix of the linked line that it cites. */
export interface PositionExplanationClaim {
  text: string;
  evidence_ref: string;
  line_id: string;
  moves: string[];
}

export interface PositionExplanationRead {
  fen: string;
  /** `template`: deterministic wording; `narrator`: configured model wording that passed grounding. */
  source: 'template' | 'narrator';
  fallback_reason: string | null;
  text: string;
  lines: PositionExplanationLine[];
  claims: PositionExplanationClaim[];
  /** Board-lines facts (schemas/board-lines-facts.schema.json). */
  facts: Record<string, unknown>;
}

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
    time_control: TimeControl | null;
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
  items: { lesson_id: string; game_id: string; ply: number; attempts_remaining: number; diagnosis: string }[];
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

export interface PuzzleReviewRead {
  run_id: string;
  initial_fen: string;
  solution: string[];
}

export interface PuzzleMoveExplanationRead {
  run: PuzzleRunRead;
  move: string;
  fen_before: string;
  fen_after: string;
  verdict: 'mistake' | 'alternative' | 'uncertain';
  summary: string;
  reply_line: string[];
  visualizations: Visualization[];
}

export interface ProgressResetRead {
  player_id: string;
  reset_at: string;
  cleared: Record<string, number>;
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

export type TrainingMode = 'review' | 'safe_or_blunder';

export interface SafeOrBlunderChoice {
  uci: string;
  san: string;
}

export interface TrainingSessionRead {
  session_id: string;
  lessons: LessonSpec[];
  move_choices: Record<string, SafeOrBlunderChoice[]>;
  attempts_used: Record<string, number>;
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
  sparring_eligible?: boolean;
  sparring?: SparringAttemptRead | null;
}

export interface GameReviewKeyMoment {
  ply: number;
  lesson_id: string;
  lesson: LessonSpec;
  attempts_used: number;
  attempts_remaining: number;
}

export interface ReviewGoodMoment {
  ply: number;
  san: string;
  skill_id: string;
  explanation: string;
  line: string[];
}

export interface AccuracyFigure {
  label: string;
  accuracy: number | null;
  scored_moves: number;
  eligible_moves: number;
}

export interface ProfileInsightsRead {
  overall: AccuracyFigure;
  phases: AccuracyFigure[];
  colours: AccuracyFigure[];
  openings: AccuracyFigure[];
  time_pressure: {
    share: number | null;
    pressure_moves: number;
    valid_clock_moves: number;
    eligible_moves: number;
  };
}

export interface GameReviewRead {
  game_id: string;
  owner_color: 'white' | 'black' | null;
  initial_fen: string;
  initial_white_probability: number | null;
  initial_sparring_eligible?: boolean;
  initial_sparring?: SparringAttemptRead | null;
  moves: GameMoveReviewRead[];
  accuracy: number | null;
  scored_moves: number;
  total_owner_moves: number;
  band_counts: Record<ReviewBand, number>;
  key_moments: GameReviewKeyMoment[];
  good_moments: ReviewGoodMoment[];
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
  attempts_used: number;
}

export interface GameCreate {
  owner_color: 'white' | 'black';
  pgn: string;
  player_id: string;
}

export type OnlineGameSource = 'lichess' | 'chesscom';

export interface OnlineGameImportRequest {
  source: OnlineGameSource;
  username: string;
  max_games: number;
}

export interface OnlineGameImportResponse {
  imported_count: number;
  duplicate_count: number;
  skipped_count: number;
  games: {
    id: string;
    white: string;
    black: string;
    result: string;
    owner_color: 'white' | 'black';
    analysis_job_id: string;
  }[];
  message: string;
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

export interface TimeControl {
  initial_seconds: number;
  increment_seconds: number;
}

export interface PlayClockRead extends TimeControl {
  white_remaining_ms: number;
  black_remaining_ms: number;
  active_color: 'white' | 'black' | null;
  deadline_at: string | null;
  server_now: string;
  version: number;
  terminal_reason: 'flag' | 'board' | 'resignation' | null;
  flagged_color: 'white' | 'black' | null;
}

export interface PlaySessionCreate {
  player_color?: 'white' | 'black' | 'random';
  player_id: string;
  game_id?: string;
  opponent_config?: Record<string, string>;
  clock_config?: TimeControl;
  initial_fen?: string;
  coach_mode?: boolean;
  sparring_source?: { game_id: string; ply: number };
}

export interface SparringAttemptRead {
  id: string;
  play_session_id: string;
  source_game_id: string;
  source_review_id: string;
  source_position_id: string;
  source_analysis_id: string;
  source_ply: number;
  source_fen: string;
  player_color: 'white' | 'black';
  seed: string;
  completed_at: string | null;
  result: '1-0' | '0-1' | '1/2-1/2' | null;
  outcome: 'win' | 'draw' | 'loss' | null;
  verification_status: 'verified' | 'historical';
  next_review_at: string | null;
}

export type AssistanceMode = 'hint' | 'candidates' | 'guard';
export type PlayAssistanceMode = Exclude<AssistanceMode, 'hint'>;

export interface PlayAssistanceRequest {
  player_id: string;
  fen: string;
  moves: string[];
  mode: PlayAssistanceMode;
}

export interface PlayMoveGrade {
  move: string;
  san: string;
  band: ReviewBand;
  win_probability_loss: number;
}

export interface PlayAssistanceRead {
  fen: string;
  generation: number;
  mode: PlayAssistanceMode;
  grades: PlayMoveGrade[];
  assistance_modes: AssistanceMode[];
}

export interface PlaySessionRead {
  player_color?: 'white' | 'black';
  opponent_move?: string | null;
  id: string;
  player_id: string;
  game_id?: string;
  opponent_config: Record<string, string>;
  clock_config?: Record<string, string>;
  clock?: PlayClockRead | null;
  pgn?: string | null;
  result?: string | null;
  status: string;
  coach_mode: boolean;
  assistance_modes: AssistanceMode[];
  assistance_tracking_version?: number | null;
  credit_analysis_status?: string | null;
  sparring?: SparringAttemptRead | null;
}

export interface PlayMoveCreate {
  move: string;
  expected_generation?: number;
  expected_clock_version?: number;
}


export interface CriticalInterruptionRead {
  lesson: LessonSpec;
  opportunity_id: string;
  study_session_id: string;
}

export interface PlayMoveResponse {
  opponent_move: string | null;
  status: string;
  clock?: PlayClockRead | null;
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
  template: string;
  total_occurrences: number;
  scored_occurrences: number;
  observed_loss: number | null;
  supporting_moves?: { game_id: string; ply: number; lesson_id: string; loss: number | null }[];
  practice_themes?: string[];
}

export type PatternReportStatus =
  | 'insufficient_data'
  | 'no_recurring_diagnosis'
  | 'recurring_diagnosis';

export interface PatternsReport {
  eligible_game_count: number;
  player_id: string;
  minimum_occurrences: number;
  status: PatternReportStatus;
  recurring_diagnoses: DiagnosisPatternRead[];
}

export interface HabitDayRead {
  utc_day: string;
  goal: number;
  qualifying_solves: number;
  met: boolean;
  withdrawn_sources: number;
}

export interface HabitTargetRead {
  id: string;
  code: string;
  status: string;
  practice_count: number;
  practice_game_count: number;
  eligible_window_count: number;
  blocking_game: { game_id: string; session_id: string; reason: 'unfinished' | 'analysis' } | null;
  invalidity_reason: string | null;
  award: Record<string, unknown> | null;
  pattern_snapshot: DiagnosisPatternRead;
}

export interface HabitSummaryRead {
  utc_day: string;
  goal: number;
  qualifying_solves: number;
  remaining: number;
  pending_goal: number | null;
  pending_effective_day: string | null;
  current_streak: number;
  best_streak: number;
  reset_at: string;
  day_status: 'in_progress';
  days: HabitDayRead[];
  supply: {
    remaining_daily_slots: number;
    eligible_lesson_retries: number;
    extra_modes_available: boolean;
  };
  targets: HabitTargetRead[];
  evidence_limits: string;
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

export interface RecurrencePeriod {
  candidate_games: number;
  games: number;
  owner_moves: number;
  occurrences: number | null;
  per_100_moves: number | null;
  wilson_95_per_100: [number, number] | null;
  excluded: Record<string, number>;
}

export interface PatternRecurrence {
  code: string;
  name: string;
  first_training_at: string | null;
  status: 'comparison' | 'not_enough_games';
  reason: string;
  before: RecurrencePeriod;
  after: RecurrencePeriod;
}

export interface PatternRecurrenceReport {
  player_id: string;
  minimum_games_per_period: number;
  minimum_owner_moves_per_game: number;
  coverage: Record<string, number>;
  patterns: PatternRecurrence[];
  caveat: string;
}

export type HealthLevel = 'good' | 'fair' | 'low';
export type HealthTrend = 'rising' | 'falling' | 'steady';

export interface PositionHealthRequest {
  player_id: string;
  fen: string;
  learner: 'white' | 'black';
  previous_fen?: string | null;
}

export interface HealthMeterRead {
  health: number;
  level: HealthLevel;
  trend: HealthTrend;
  terms: { name: string; pawns: number }[];
}

export interface PositionHealthRead {
  fen: string;
  learner: 'white' | 'black';
  overall: { key: string; name: string; learner: HealthMeterRead; opponent: HealthMeterRead };
  groups: { key: string; name: string; learner: HealthMeterRead; opponent: HealthMeterRead }[];
  // Set when the game is over; the panel shows the result instead of the meters.
  outcome: 'checkmate' | 'stalemate' | 'insufficient_material' | 'seventyfive_moves' | null;
}
