import { useCallback, useEffect, useState } from 'react';
import { ApiClient, getActivePlayerId } from '../api/client';
import { OnboardingCard } from './OnboardingCard';
import { StrengthSummary } from './StrengthSummary';
import type {
  EvidenceReport,
  PatternsReport,
  PlayerProgressReport,
  StrengthRead,
} from '../api/types';

export function ProfileScreen({ onNavigate }: { onNavigate?: (path: string) => void } = {}) {
  const [progress, setProgress] = useState<PlayerProgressReport | null>(null);
  const [evidence, setEvidence] = useState<EvidenceReport | null>(null);
  const [patterns, setPatterns] = useState<PatternsReport | null>(null);
  const [strength, setStrength] = useState<StrengthRead | null>(null);
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
    ])
      .then(([progressResponse, evidenceResponse, patternsResponse]) => {
        if (cancelled) return;
        setProgress(progressResponse);
        setEvidence(evidenceResponse);
        setPatterns(patternsResponse);
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

  if (!progress || !evidence || !patterns) {
    return <div role="status">Loading profile...</div>;
  }

  return (
    <div className="profile-screen" data-testid="profile-screen">
      <h2>Player Profile: {playerId}</h2>
      <OnboardingCard onNavigate={onNavigate} onChange={refreshStrength} />
      {strengthError ? <div role="alert"><p>{strengthError}</p><button onClick={refreshStrength}>Retry strength report</button></div>
        : strength ? <StrengthSummary strength={strength} /> : <p role="status">Loading strength report…</p>}

      <section>
        <h3>Strengths & Weaknesses</h3>
        <p>Skills include verified practice and eligible completed-game tactical successes. Review a game to inspect good-move proof and exclusions; “no in-app hint” does not establish absence of external help.</p>
        <p>New skills begin with a provisional (3, 2) seed, not a measurement of your ability. Observed evidence updates those priors; the internal profile rating is not a playing-strength estimate.</p>
        <ul>
          {progress.skills.map((skill) => (
            <li key={skill.concept}>
              {skill.name}: {skill.mastery.toFixed(2)} (Uncertainty: {skill.uncertainty.toFixed(2)})
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Evidence</h3>
        <ul>
          {evidence.evidence_items.length === 0 ? <li>No evidence collected yet.</li> : null}
          {evidence.evidence_items.map((item) => (
            <li key={item.evidence_id}>{item.kind}: {item.claim}</li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Recurring Diagnoses</h3>
        <ul>
          {patterns.status === 'insufficient_data' ? (
            <li>More analysed games are needed before recurrence can be assessed.</li>
          ) : null}
          {patterns.status === 'no_recurring_diagnosis' ? (
            <li>No diagnosis has recurred across {patterns.minimum_occurrences} games.</li>
          ) : null}
          {patterns.recurring_diagnoses.map((diagnosis) => (
            <li key={diagnosis.diagnosis}>
              {diagnosis.name}: {diagnosis.occurrence_count} games
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
