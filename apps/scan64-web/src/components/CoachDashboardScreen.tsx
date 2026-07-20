import { useEffect, useState } from 'react';
import { ApiClient, getOrCreatePlayerId } from '../api/client';
import type { CoachDashboard, PatternRead } from '../api/types';
import './CoachDashboardScreen.css';

function patternLabel(pattern: PatternRead, index: number): string {
  if (pattern.description) return pattern.description;
  if (pattern.rule_id) return pattern.rule_id;
  return `Observed pattern ${index + 1}`;
}

function evidenceSourceLabel(producer: Record<string, unknown>): string | null {
  const name = producer.name;
  const version = producer.version;
  if (typeof name !== 'string') return null;
  return typeof version === 'string' ? `${name} · ${version}` : name;
}

export function CoachDashboardScreen() {
  const coachId = getOrCreatePlayerId();
  const [dashboard, setDashboard] = useState<CoachDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    void ApiClient.getCoachDashboard(coachId)
      .then((response) => {
        if (!cancelled) setDashboard(response);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(cause instanceof Error ? cause.message : 'Coach dashboard request failed');
        }
      });

    return () => {
      cancelled = true;
    };
  }, [coachId]);

  if (error) {
    return (
      <section className="coach-dashboard coach-dashboard--error" aria-live="polite">
        <p className="coach-dashboard__eyebrow">Coach workspace</p>
        <h2>Dashboard unavailable</h2>
        <p>{error}</p>
      </section>
    );
  }

  if (!dashboard) {
    return <div data-testid="coach-dashboard-loading">Loading coach dashboard...</div>;
  }

  return (
    <section className="coach-dashboard" data-testid="coach-dashboard">
      <header className="coach-dashboard__header">
        <div>
          <p className="coach-dashboard__eyebrow">Coach workspace</p>
          <h2>Pattern ledger</h2>
          <p>Evidence-backed observations from students who chose to share their study record.</p>
        </div>
        <p className="coach-dashboard__identity">Coach {dashboard.coach_id}</p>
      </header>

      {dashboard.students.length === 0 ? (
        <div className="coach-dashboard__empty">
          <h3>No linked students</h3>
          <p>Students appear here after they explicitly link their account to this coach.</p>
        </div>
      ) : (
        <div className="coach-dashboard__students">
          {dashboard.students.map((student) => (
            <article className="coach-student" key={student.student_id}>
              <header className="coach-student__header">
                <div>
                  <p className="coach-dashboard__eyebrow">Student dossier</p>
                  <h3>{student.profile.display_name ?? student.student_id}</h3>
                </div>
                <p className="coach-student__rating">Rating {student.profile.rating}</p>
              </header>

              <section className="coach-student__section" aria-labelledby={`${student.student_id}-patterns`}>
                <h4 id={`${student.student_id}-patterns`}>Observed patterns</h4>
                {student.patterns.recurring_habits.length === 0 ? (
                  <p className="coach-student__muted">No recurring habits have met the evidence threshold.</p>
                ) : (
                  <ul className="coach-student__patterns">
                    {student.patterns.recurring_habits.map((pattern, index) => (
                      <li key={pattern.rule_id ?? `${student.student_id}-${index}`}>
                        {patternLabel(pattern, index)}
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              <section className="coach-student__section" aria-labelledby={`${student.student_id}-evidence`}>
                <h4 id={`${student.student_id}-evidence`}>Evidence trail</h4>
                {student.evidence.evidence_items.length === 0 ? (
                  <p className="coach-student__muted">No verified evidence has been recorded yet.</p>
                ) : (
                  <ul className="coach-student__evidence">
                    {student.evidence.evidence_items.map((item) => {
                      const source = evidenceSourceLabel(item.producer);
                      return (
                        <li key={item.evidence_id}>
                          <p>{item.claim}</p>
                          <div>
                            <span>{item.kind}</span>
                            <span>Position {item.position_id}</span>
                            {source ? <span>{source}</span> : null}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}
