import { useEffect, useState } from 'react';
import { ApiClient, getOrCreatePlayerId } from '../api/client';
import type {
  EvidenceReport,
  PatternsReport,
  PlayerProgressReport,
} from '../api/types';

export function ProfileScreen() {
  const [progress, setProgress] = useState<PlayerProgressReport | null>(null);
  const [evidence, setEvidence] = useState<EvidenceReport | null>(null);
  const [patterns, setPatterns] = useState<PatternsReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const playerId = getOrCreatePlayerId();

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

  if (error) {
    return <div data-testid="profile-error">Profile unavailable: {error}</div>;
  }

  if (!progress || !evidence || !patterns) {
    return <div>Loading profile...</div>;
  }

  return (
    <div className="profile-screen" data-testid="profile-screen">
      <h2>Player Profile: {playerId}</h2>

      <section>
        <h3>Strengths & Weaknesses</h3>
        <ul>
          {progress.skills.map((skill) => (
            <li key={skill.concept}>
              {skill.concept}: {skill.mastery.toFixed(2)} (Uncertainty: {skill.uncertainty.toFixed(2)})
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
              {diagnosis.diagnosis}: {diagnosis.occurrence_count} games
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
