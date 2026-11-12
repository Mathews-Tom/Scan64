import { useEffect, useState } from 'react';
import { PatternCard } from './PatternCard';
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
    return <div role="alert" data-testid="profile-error">Profile unavailable: {error}</div>;
  }

  if (!progress || !evidence || !patterns) {
    return <div role="status">Loading profile...</div>;
  }

  return (
    <div className="profile-screen" data-testid="profile-screen">
      <h2>Player Profile: {playerId}</h2>

      <section>
        <h3>Strengths & Weaknesses</h3>
        <p>Skills include verified practice and eligible completed-game tactical successes. Review a game to inspect good-move proof and exclusions; “no in-app hint” does not establish absence of external help.</p>
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

      <PatternCard report={patterns} />
    </div>
  );
}
