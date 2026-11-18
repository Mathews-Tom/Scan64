import { useCallback, useEffect, useState } from 'react';
import { ApiClient, getActivePlayerId } from '../api/client';
import { OnboardingCard } from './OnboardingCard';
import { StrengthSummary } from './StrengthSummary';
import { HabitPanel } from './HabitPanel';
import { Panel } from './Primitives';
import type {
  EvidenceReport,
  PatternsReport,
  PlayerProgressReport,
  ProfileInsightsRead,
  StrengthRead,
} from '../api/types';

export function ProfileScreen({ onNavigate }: { onNavigate?: (path: string) => void } = {}) {
  const [progress, setProgress] = useState<PlayerProgressReport | null>(null);
  const [evidence, setEvidence] = useState<EvidenceReport | null>(null);
  const [patterns, setPatterns] = useState<PatternsReport | null>(null);
  const [strength, setStrength] = useState<StrengthRead | null>(null);
  const [insights, setInsights] = useState<ProfileInsightsRead | null>(null);
  const [strengthError, setStrengthError] = useState<string | null>(null);
  const [strengthRequest, setStrengthRequest] = useState(0);
  const refreshStrength = useCallback(() => setStrengthRequest(value => value + 1), []);
  const [error, setError] = useState<string | null>(null);
  const playerId = getActivePlayerId();

  useEffect(() => {
    let cancelled = false;

    void Promise.all([
      ApiClient.getPlayerProgress(playerId),
      ApiClient.getPlayerEvidence(playerId),
      ApiClient.getPlayerPatterns(playerId),
      ApiClient.getPlayerInsights(playerId),
    ])
      .then(([progressResponse, evidenceResponse, patternsResponse, insightsResponse]) => {
        if (cancelled) return;
        setProgress(progressResponse);
        setEvidence(evidenceResponse);
        setPatterns(patternsResponse);
        setInsights(insightsResponse);
      })
      .catch((requestError: unknown) => {
        if (cancelled) return;
        setError(requestError instanceof Error ? requestError.message : 'Profile unavailable');
      });

    return () => {
      cancelled = true;
    };
  }, [playerId]);

  useEffect(() => {
    let cancelled = false;
    setStrengthError(null);
    void ApiClient.getPlayerStrength(playerId).then(result => {
      if (!cancelled) setStrength(result);
    }).catch(cause => {
      if (!cancelled) setStrengthError(cause instanceof Error ? cause.message : 'Strength report unavailable');
    });
    return () => { cancelled = true; };
  }, [playerId, strengthRequest]);

  if (error) {
    return <div role="alert" data-testid="profile-error">Profile unavailable: {error}</div>;
  }

  if (!progress || !evidence || !patterns || !insights) {
    return <div role="status">Loading profile...</div>;
  }

  return (
    <div className="profile-screen working-screen" data-testid="profile-screen">
      <h1>Profile</h1>
      <OnboardingCard onNavigate={onNavigate} onChange={refreshStrength} />
      <Panel title="Your strength record">
        {strengthError ? <div role="alert"><p>{strengthError}</p><button onClick={refreshStrength}>Retry strength report</button></div>
          : strength ? <StrengthSummary strength={strength} /> : <p role="status">Loading strength report…</p>}
      </Panel>
      <Panel title="Your game insights">
        <p>Scan64 accuracy pools scored owner moves, not game percentages or external-site accuracy.</p>
        {([
          ['Overall', [insights.overall]],
          ['By phase', insights.phases],
          ['By colour', insights.colours],
          ['By opening family', insights.openings],
        ] as const).map(([title, figures]) => <section key={title} aria-label={title}>
          <h3>{title}</h3>
          <ul>{figures.map(figure => <li key={figure.label}>
            {figure.label}: {figure.accuracy === null ? 'Unavailable' : `${figure.accuracy.toFixed(1)}%`}
            {' · '}{figure.scored_moves} / {figure.eligible_moves} scored / eligible owner moves
          </li>)}</ul>
        </section>)}
        <p>Phase uses the position before your move: opening is the first ten full moves;
          then endgame means combined non-pawn, non-king material ≤26 (Q=9, R=5, B=N=3), otherwise middlegame.</p>
        <h3>Time pressure</h3>
        <p>{insights.time_pressure.share === null ? 'Unavailable' : `${insights.time_pressure.share.toFixed(1)}%`}
          {' · '}{insights.time_pressure.pressure_moves} / {insights.time_pressure.valid_clock_moves} valid-clock owner moves at ≤30s recorded post-move clock.</p>
        <p>Supported real-time TimeControl games only (each stage under 24 hours, no correspondence); clock coverage: {insights.time_pressure.valid_clock_moves} / {insights.time_pressure.eligible_moves} eligible owner moves.
          Clocks must match the stored mainline. Clockless data is unavailable, not zero.</p>
      </Panel>

      <div className="profile-records">
      <Panel title="Strengths and weaknesses">
        <p>Skills include verified practice and eligible completed-game tactical successes. Review a game to inspect good-move proof and exclusions; “no in-app hint” does not establish absence of external help.</p>
        <p>New skills begin with a provisional (3, 2) seed, not a measurement of your ability. Observed evidence updates those priors; the internal profile rating is not a playing-strength estimate.</p>
        <ul>
          {progress.skills.map((skill) => (
            <li key={skill.concept}>
              {skill.name}: {skill.mastery.toFixed(2)} (Uncertainty: {skill.uncertainty.toFixed(2)})
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Evidence">
        <ul>
          {evidence.evidence_items.length === 0 ? <li>No evidence collected yet.</li> : null}
          {evidence.evidence_items.map((item) => (
            <li key={item.evidence_id}>{item.kind}: {item.claim}</li>
          ))}
        </ul>
      </Panel>
      </div>

      <HabitPanel playerId={playerId} patterns={patterns} onNavigate={onNavigate} />
    </div>
  );
}
