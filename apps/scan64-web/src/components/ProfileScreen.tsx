import { useEffect, useState } from 'react';
import { getOrCreatePlayerId } from '../api/client';

export function ProfileScreen() {
  const [progress, setProgress] = useState<any>(null);
  const [evidence, setEvidence] = useState<any>(null);
  const [patterns, setPatterns] = useState<any>(null);
  const playerId = getOrCreatePlayerId();

  useEffect(() => {
    fetch(`/api/v1/players/${playerId}/progress`).then(res => res.json()).then(setProgress);
    fetch(`/api/v1/players/${playerId}/evidence`).then(res => res.json()).then(setEvidence);
    fetch(`/api/v1/players/${playerId}/patterns`).then(res => res.json()).then(setPatterns);
  }, [playerId]);

  if (!progress || !evidence || !patterns) {
    return <div>Loading profile...</div>;
  }

  return (
    <div className="profile-screen" data-testid="profile-screen">
      <h2>Player Profile: {playerId}</h2>
      
      <section>
        <h3>Strengths & Weaknesses</h3>
        <ul>
          {progress.skills?.map((s: any) => (
            <li key={s.concept}>
              {s.concept}: {s.mastery.toFixed(2)} (Uncertainty: {s.uncertainty.toFixed(2)})
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Evidence</h3>
        <ul>
          {evidence.evidence_items?.length === 0 ? <li>No evidence collected yet.</li> : null}
          {evidence.evidence_items?.map((e: any, i: number) => (
            <li key={i}>{e.kind}: {e.claim}</li>
          ))}
        </ul>
      </section>

      <section>
        <h3>Recurring Habits</h3>
        <ul>
          {patterns.recurring_habits?.length === 0 ? <li>No habits identified yet.</li> : null}
          {patterns.recurring_habits?.map((h: any, i: number) => (
            <li key={i}>{h.description}</li>
          ))}
        </ul>
      </section>
    </div>
  );
}
