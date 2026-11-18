import { useEffect, useState } from 'react';
import { ApiClient } from '../api/client';
import type { PatternRecurrenceReport, RecurrencePeriod } from '../api/types';
import { Panel } from './Primitives';

function Coverage({ values }: { values: Record<string, number> }) {
  return <ul>{Object.entries(values).map(([reason, count]) => <li key={reason}>
    {reason.replaceAll('_', ' ')}: {count}
  </li>)}</ul>;
}

function Period({ name, period }: { name: string; period: RecurrencePeriod }) {
  return <section aria-label={name}>
    <h4>{name}</h4>
    <p>{period.games} games · {period.owner_moves} owner moves · {period.occurrences === null ? 'Occurrences unavailable' : `${period.occurrences} occurrences`}</p>
    <p>{period.per_100_moves === null ? 'Rate unavailable' : `${period.per_100_moves.toFixed(2)} occurrences per 100 owner moves`}</p>
    {period.wilson_95_per_100 ? <p>95% Wilson interval: {period.wilson_95_per_100[0].toFixed(2)}–{period.wilson_95_per_100[1].toFixed(2)} per 100 owner moves</p> : null}
    <p>Coverage: {period.games} / {period.candidate_games} dated finished online games eligible.</p>
    <Coverage values={period.excluded} />
  </section>;
}

export function PatternRecurrencePanel({ playerId }: { playerId: string }) {
  const [report, setReport] = useState<PatternRecurrenceReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setReport(null);
    setError(null);
    async function load() {
      try {
        const result = await ApiClient.getPatternRecurrence(playerId);
        if (!cancelled) setReport(result);
      } catch (cause) {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Please try again.');
      }
    }
    void load();
    return () => { cancelled = true; };
  }, [playerId, request]);

  return <Panel title="Progress: before/after patterns" aria-label="Progress: before/after patterns">
    <p>Imported online games around your first completed, server-verified mapped training attempt since reset, correct or incorrect. Periods use PGN UTC start times, never import time.</p>
    {error ? <div role="alert"><p>Pattern comparison unavailable: {error}</p></div> : null}
    {!report && !error ? <p role="status">Loading pattern comparison…</p> : null}
    {report ? <>
      <p>Each side needs {report.minimum_games_per_period} finished, fully analysed games with at least {report.minimum_owner_moves_per_game} owner moves per game. Missing evidence is unavailable, not zero.</p>
      <p>{report.caveat}</p>
      <details><summary>Library coverage and exclusions</summary><Coverage values={report.coverage} /></details>
      {report.patterns.map(pattern => <section key={pattern.code} aria-label={pattern.name}>
        <h3>{pattern.name}</h3>
        <p><strong>{pattern.status === 'comparison' ? 'Before/after comparison' : 'Not enough games'}</strong></p>
        <p>{pattern.reason}</p>
        {pattern.first_training_at ? <p>First verified training: <time dateTime={pattern.first_training_at}>{pattern.first_training_at}</time></p> : null}
        <Period name="Before training" period={pattern.before} />
        <Period name="After training" period={pattern.after} />
      </section>)}
    </> : null}
    <button onClick={() => setRequest(value => value + 1)}>{error ? 'Retry pattern comparison' : 'Refresh pattern comparison'}</button>
  </Panel>;
}
