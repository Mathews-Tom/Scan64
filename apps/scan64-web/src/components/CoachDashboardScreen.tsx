import { PatternCard } from './PatternCard';
import { useEffect, useState } from 'react';
import { ApiClient, getOrCreatePlayerId } from '../api/client';
import type { CoachDashboard } from '../api/types';
import { EmptyState, Panel } from './Primitives';
import { StrengthSummary } from './StrengthSummary';


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
      <Panel className="coach-dashboard--error" role="alert">
        <p className="meta">Coach workspace</p>
        <h1>Dashboard unavailable</h1>
        <p>{error}</p>
      </Panel>
    );
  }

  if (!dashboard) {
    return <div role="status" data-testid="coach-dashboard-loading">Loading coach dashboard...</div>;
  }

  return (
    <section className="coach-dashboard working-screen" data-testid="coach-dashboard">
      <header className="coach-dashboard__header">
        <div>
          <p className="meta">Coach workspace</p>
          <h1>Pattern ledger</h1>
          <p>Evidence-backed observations from students who chose to share their study record.</p>
        </div>
      </header>

      {dashboard.students.length === 0 ? (
        <EmptyState title="No linked students">
          <p>Students appear here after they explicitly link their account to this coach.</p>
        </EmptyState>
      ) : (
        <div className="coach-dashboard__students">
          {dashboard.students.map((student) => (
            <Panel className="coach-student" key={student.student_id}>
              <header className="coach-student__header">
                <div>
                  <p className="meta">Student dossier</p>
                  <h2>{student.profile.display_name ?? 'Unnamed student'}</h2>
                </div>
              </header>
              <StrengthSummary strength={student.strength} />

              <section className="coach-student__section" aria-labelledby={`${student.student_id}-patterns`}>
                <h3 id={`${student.student_id}-patterns`}>Recurring diagnoses</h3>
                <PatternCard report={student.patterns} owner={false} />
              </section>

              <section className="coach-student__section" aria-labelledby={`${student.student_id}-evidence`}>
                <h3 id={`${student.student_id}-evidence`}>Evidence trail</h3>
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
                            {source ? <span>{source}</span> : null}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </section>
            </Panel>
          ))}
        </div>
      )}
    </section>
  );
}
