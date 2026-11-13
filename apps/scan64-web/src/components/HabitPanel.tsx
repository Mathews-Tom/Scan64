import { useEffect, useId, useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import type { HabitSummaryRead, HabitTargetRead, PatternsReport, PuzzleStreakRead } from '../api/types';
import { PatternCard } from './PatternCard';
import './HabitPanel.css';

const TARGET_STATUSES: Record<string, string> = {
  practicing: 'Collecting qualifying practice',
  observing: 'Observing subsequent eligible games',
  met: 'Observation window complete',
  invalidated: 'Current pattern evidence is invalid',
};

function TargetEvidence({ target, pending, onRefresh }: {
  target: HabitTargetRead;
  pending: boolean;
  onRefresh: () => void;
}) {
  const status = TARGET_STATUSES[target.status] ?? target.status;

  return <div className="habit-target">
    <h3>Tracked practice target</h3>
    <p>Status: {status}.</p>
    {target.award ? <>
      <p><strong>Pattern practice target met</strong>{target.invalidity_reason ? ' (historical award)' : ''}</p>
      <p>{target.invalidity_reason ? 'At the time of the award: ' : 'Recorded evidence: '}not observed in the last five eligible games.</p>
    </> : null}
    {target.invalidity_reason ? <p>Current evidence unavailable: {target.invalidity_reason}. Historical awards and met calendar days are preserved.</p> : null}
    <p>Practice evidence: {target.practice_count}/3 qualifying first-attempt successes at distinct source positions, across {target.practice_game_count}/2 required games.</p>
    <p>Absence evidence: {target.eligible_window_count}/5 subsequent eligible games in the current window.</p>
    {target.blocking_game ? <p>
      {target.blocking_game.reason === 'unfinished' ? <>Waiting for this unfinished game: <a href={`/play?session=${encodeURIComponent(target.blocking_game.session_id)}`}>Resume or resign game {target.blocking_game.game_id}</a>.</> :
        <>Waiting for an earlier game’s analysis: <a href={`/games/${encodeURIComponent(target.blocking_game.game_id)}/analysis`}>Review game {target.blocking_game.game_id}</a>.</>}
    </p> : null}
    <p>Only terminal, fully analysed Scan64 play-session games with known owner colour, every owner move scored and at least 20 owner moves qualify. Imports do not count. A retained target diagnosis or an uncapped non-retired observation restarts the window.</p>
    <p>Lesson hints are app-reported, not server-proven independence. Diagnosis uses a ≥150cp fast-pass swing threshold; smaller or undetected errors can remain. This is bounded evidence, not a cure or permanent mastery.</p>
    <details>
      <summary>Inspect target evidence</summary>
      <pre>{JSON.stringify({ pattern_at_start: target.pattern_snapshot, practice_count: target.practice_count,
        practice_game_count: target.practice_game_count, eligible_window_count: target.eligible_window_count,
        award: target.award, invalidity_reason: target.invalidity_reason }, null, 2)}</pre>
    </details>
    <button disabled={pending} onClick={onRefresh}>Refresh tracked evidence for {target.pattern_snapshot.name}</button>
    <p className="muted">Refresh saves any current invalidity reason; reading this page never awards or withdraws progress.</p>
  </div>;
}

export function HabitPanel({ playerId, patterns, onNavigate }: {
  playerId: string;
  patterns: PatternsReport;
  onNavigate?: (path: string) => void;
}) {
  const [summary, setSummary] = useState<HabitSummaryRead | null>(null);
  const [streak, setStreak] = useState<PuzzleStreakRead | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [streakError, setStreakError] = useState<string | null>(null);
  const [goalInput, setGoalInput] = useState('3');
  const [pending, setPending] = useState(false);
  const [request, setRequest] = useState(0);
  const [message, setMessage] = useState('');
  const generation = useRef(0);
  const errorRef = useRef<HTMLDivElement>(null);
  const messageRef = useRef<HTMLParagraphElement>(null);
  const goalId = useId();
  const helpId = useId();

  useEffect(() => {
    const current = ++generation.current;
    setError(null);
    setStreakError(null);
    setPending(false);
    setMessage('');
    setSummary(null);
    setStreak(null);
    void ApiClient.getPlayerHabit(playerId).then(result => {
      if (generation.current !== current) return;
      setSummary(result);
      setGoalInput(String(result.pending_goal ?? result.goal));
    }).catch((cause: unknown) => {
      if (generation.current === current) setError(`Habit record unavailable: ${cause instanceof Error ? cause.message : 'Please try again.'}`);
    });
    void ApiClient.getPuzzleModes().then(result => {
      if (generation.current === current) setStreak(result.streak);
    }).catch((cause: unknown) => {
      if (generation.current === current) setStreakError(`Puzzle solve streak unavailable: ${cause instanceof Error ? cause.message : 'Please try again.'}`);
    });
    return () => { generation.current += 1; };
  }, [playerId, request]);

  useEffect(() => { if (error || streakError) errorRef.current?.focus(); }, [error, streakError]);
  useEffect(() => { if (message) messageRef.current?.focus(); }, [message]);

  async function act(action: () => Promise<HabitSummaryRead>, success: string) {
    const current = generation.current;
    setPending(true);
    setError(null);
    setMessage('');
    try {
      const result = await action();
      if (generation.current !== current) return;
      setSummary(result);
      setGoalInput(String(result.pending_goal ?? result.goal));
      setMessage(success);
    } catch (cause: unknown) {
      if (generation.current === current) setError(`Habit change could not be saved: ${cause instanceof Error ? cause.message : 'Please try again.'}`);
    } finally {
      if (generation.current === current) setPending(false);
    }
  }

  const top = patterns.status === 'recurring_diagnosis' ? patterns.recurring_diagnoses[0] : undefined;
  const topTarget = summary?.targets.find(target => target.code === top?.diagnosis);
  const otherTargets = summary?.targets.filter(target => target.code !== top?.diagnosis) ?? [];

  return <div className="habit-panels">
    <section className="study-panel habit-daily" aria-label="Daily qualifying-solve goal">
      <h2>Your daily qualifying solves</h2>
      {(error || streakError) ? <div role="alert" tabIndex={-1} ref={errorRef}>
        {error ? <p>{error}</p> : null}
        {streakError ? <p>{streakError}</p> : null}
        <button disabled={pending} onClick={() => setRequest(value => value + 1)}>Retry habit record</button>
      </div> : null}
      {message ? <p role="status" tabIndex={-1} ref={messageRef}>{message}</p> : null}
      {!summary && !error ? <p role="status">Loading daily goal…</p> : null}
      {summary ? <>
        <p><time dateTime={summary.utc_day}>{summary.utc_day}</time> UTC: {summary.qualifying_solves}/{summary.goal} qualifying solves · {summary.remaining} remaining.</p>
        <p>Today is in progress until <time dateTime={summary.reset_at}>{summary.reset_at}</time> (UTC), even when the goal is reached.</p>
        <p><strong>Calendar-day streak:</strong> {summary.current_streak} current · {summary.best_streak} best UTC days. A missed completed day breaks this streak.</p>
        <p><strong>Available supply:</strong> {summary.supply.remaining_daily_slots} unplayed daily slots · {summary.supply.eligible_lesson_retries} eligible lesson retries · Extra puzzle modes {summary.supply.extra_modes_available ? 'available' : 'unavailable'}.</p>
        <p>Supply is not a promise of qualifying solves. Repeated sources and earlier lesson attempts can make retries ineligible. Short supply never lowers your goal.</p>
        <form onSubmit={event => {
          event.preventDefault();
          const goal = Number(goalInput);
          if (!Number.isSafeInteger(goal) || goal < 1) {
            setError('Enter a positive whole-number goal. Today’s goal will not change.');
            return;
          }
          void act(() => ApiClient.updateHabitGoal(playerId, goal), 'Goal saved for the next UTC day. Today’s goal is unchanged.');
        }}>
          <label htmlFor={goalId}>Qualifying solves per day</label>
          <input id={goalId} type="number" min="1" step="1" required value={goalInput} disabled={pending}
            aria-describedby={helpId} onChange={event => setGoalInput(event.target.value)} />
          <p id={helpId}>Positive whole numbers only. Every goal edit takes effect next UTC day, not today.</p>
          <button type="submit" disabled={pending}>{pending ? 'Saving habit change…' : 'Save next-day goal'}</button>
        </form>
        {summary.pending_goal !== null ? <p>Scheduled goal: {summary.pending_goal} qualifying solves from {summary.pending_effective_day} UTC.</p> : null}
        <h3>UTC day history</h3>
        {summary.days.length ? <ol className="habit-calendar" aria-label="UTC day history">
          {summary.days.map(day => <li key={day.utc_day}>
            <time dateTime={day.utc_day}>{day.utc_day}</time>: {day.qualifying_solves}/{day.goal} · {day.utc_day === summary.utc_day ? (day.met ? 'Goal reached; in progress' : 'In progress') : day.met ? 'Met' : 'Not met'}
            {day.withdrawn_sources > 0 ? <span> · {day.withdrawn_sources} withdrawn source(s); historical met status preserved</span> : null}
          </li>)}
        </ol> : <p>No recorded days yet. Only post-cutover qualifying results count.</p>}
        <p>{summary.evidence_limits}</p>
        <p>Puzzles require a terminal server-unhinted solve and matching attempt. Visible, non-retired, owned trusted persisted-opportunity lessons require server-graded success on the first recorded opportunity attempt and app-reported zero hints. Lesson answers and hints are available to the app: this does not prove independent play.</p>
        <p>Repeated positions across modes count once per UTC day. Starts, time, hints, wrong answers, exhausted or foreign lessons, ungraded, transfer and opening attempts do not count. Goals and targets add no mastery or rating bonuses.</p>
        <button disabled={pending} onClick={() => setRequest(value => value + 1)}>Refresh habit record</button>
      </> : null}
      <h3>Puzzle solve streak (separate)</h3>
      {streak ? <p>{streak.current} current · {streak.best} best consecutive qualifying puzzle solves.{streak.reset_reason ? ` Reset reason: ${streak.reset_reason}.` : ''}</p>
        : !streakError ? <p role="status">Loading puzzle solve streak…</p> : null}
      <p>This is the puzzle mode’s solve streak, not a calendar-day streak.</p>
    </section>
    <PatternCard report={patterns} onNavigate={onNavigate}>
      {topTarget ? <TargetEvidence target={topTarget} pending={pending} onRefresh={() => void act(
        () => ApiClient.refreshHabitTarget(playerId, topTarget.id), 'Tracked evidence refreshed. Historical awards and met days are preserved.',
      )} />
        : summary && top && patterns.status === 'recurring_diagnosis' ? <div className="habit-target">
          <button disabled={pending} onClick={() => void act(
            () => ApiClient.startHabitTarget(playerId, top.diagnosis), 'Pattern target tracked. Changes to the top recurring pattern will not switch this target.',
          )}>Track practice target for {top.name}</button>
          <p>Track three first-recorded-attempt same-pattern successes at distinct source positions across at least two games, then five eligible games. Short or exhausted retry supply means insufficient evidence; Theme runs cannot substitute.</p>
          <p>Lesson hints are app-reported. Diagnosis uses a ≥150cp threshold. Tracking is not a mastery or cure claim.</p>
        </div> : null}
    </PatternCard>
    {otherTargets.map(target => <section className="study-panel" aria-label={`Tracked pattern: ${target.pattern_snapshot.name}`} key={target.id}>
      <h2>Tracked pattern: {target.pattern_snapshot.name}</h2>
      <p>This target stays tracked even when the top recurring pattern changes. The following coverage was saved when tracking began.</p>
      <p>{target.pattern_snapshot.template}</p>
      <p>Support at start: {target.pattern_snapshot.occurrence_count} distinct games · Coverage at start: {target.pattern_snapshot.scored_occurrences}/{target.pattern_snapshot.total_occurrences} scored retained moments.</p>
      <ul>{target.pattern_snapshot.supporting_moves?.map(move => <li key={`${move.game_id}-${move.ply}`}>
        <a href={`/games/${encodeURIComponent(move.game_id)}/analysis?ply=${move.ply}`}>Game {move.game_id}, ply {move.ply}</a>
      </li>)}</ul>
      <TargetEvidence target={target} pending={pending} onRefresh={() => void act(
        () => ApiClient.refreshHabitTarget(playerId, target.id), 'Tracked evidence refreshed. Historical awards and met days are preserved.',
      )} />
    </section>)}
  </div>;
}
