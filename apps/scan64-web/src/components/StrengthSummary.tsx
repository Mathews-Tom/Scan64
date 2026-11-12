import type { BotResultsSample, StrengthRead } from '../api/types';
import { DECLARED_LEVEL_LABELS } from './OnboardingCard';
import './Strength.css';

function sampleText(sample: BotResultsSample): string {
  return `${sample.sample_size} games: ${sample.wins} wins, ${sample.draws} draws, ${sample.losses} losses`;
}

export function StrengthSummary({ strength }: { strength: StrengthRead }) {
  return <div className="strength-summary">
    <section>
      <h3>Declared experience and bot suggestion</h3>
      <p>Declared level: {strength.declared_level ? DECLARED_LEVEL_LABELS[strength.declared_level] : 'Not declared'}.</p>
      <p>Suggested bot level: {strength.suggested_bot_level ?? 'None yet'}. A starting choice, not measured chess strength.</p>
    </section>
    <section>
      <h3>Puzzle Elo and event trend</h3>
      <p>Scan64 puzzle rating: {strength.puzzle_rating}{strength.puzzle_provisional ? ' (provisional)' : ''} · {strength.puzzle_rated_count} rated puzzles</p>
      <p>Initial 1200; K-factor 32; provisional for the first 20 rated puzzles. This separate practice scale is not chess, FIDE or site Elo, and is not theme mastery.</p>
      {strength.puzzle_events.length ? <ol aria-label="Puzzle rating events">
        {strength.puzzle_events.map(event => <li key={event.sequence}>
          Event {event.sequence}: {event.before} to {event.after} ({event.after - event.before >= 0 ? '+' : ''}{event.after - event.before}); score {event.score}.
        </li>)}
      </ol> : <p>No rated puzzle events yet.</p>}
    </section>
    <section>
      <h3>Stockfish results by level and policy</h3>
      {strength.bot_cohorts.length ? <ul className="bot-cohorts">{strength.bot_cohorts.map(cohort => <li key={`${cohort.provider}-${cohort.bot_level}-${cohort.bot_policy_version}`}>
        <h4>Stockfish level {cohort.bot_level} · policy {cohort.bot_policy_version}</h4>
        <p>Total sample: {sampleText(cohort)}.</p>
        <p>Latest five: {sampleText(cohort.latest)}.</p>
        <p>Previous five: {sampleText(cohort.previous)}.</p>
        <p>Trend: {cohort.trend === 'insufficient_data' ? 'Insufficient data (five games in each window required)' : cohort.trend}.</p>
      </li>)}</ul> : <p>No eligible bot results yet.</p>}
      <p>{strength.disclosure}</p>
      <p>Relative bot results are not human Elo. Levels never change automatically. Legacy v1(unstamped) results are separate from stamped policy cohorts. No recorded hints does not certify no takebacks or external help.</p>
    </section>
  </div>;
}
