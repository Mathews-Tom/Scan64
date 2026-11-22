import { useState } from 'react';
import { getStoredPlayerId } from '../api/client';
import { RepertoireApi, type RepertoireDeviationReport } from '../api/repertoire';
import { Button } from './Primitives';

type Result = { report: RepertoireDeviationReport } | { error: string };

export function RepertoireDeviations({ repertoireId, blocked, practise }: {
  repertoireId: string;
  blocked: boolean;
  practise: (nodeId: number) => void;
}) {
  const [result, setResult] = useState<{ owner: string | null; value: Result }>();
  const [loading, setLoading] = useState(false);
  const value = result && result.owner === getStoredPlayerId() ? result.value : undefined;
  const report = value && 'report' in value ? value.report : undefined;
  const compare = async () => {
    const owner = getStoredPlayerId();
    setLoading(true);
    setResult(undefined);
    try {
      const comparison = await RepertoireApi.deviations(repertoireId);
      if (owner === getStoredPlayerId()) setResult({ owner, value: { report: comparison } });
    } catch (caught) {
      if (owner === getStoredPlayerId()) {
        setResult({ owner, value: { error: caught instanceof Error ? caught.message : 'Could not compare imported games.' } });
      }
    } finally {
      setLoading(false);
    }
  };

  return <section aria-label="Repertoire departures">
    <h5>Repertoire departures</h5>
    <Button disabled={blocked || loading} onClick={() => void compare()}>Compare imported games</Button>
    <p>Games linked to a Play session are not included.</p>
    {loading && <p role="status">Comparing imported games…</p>}
    {value && 'error' in value && <p role="alert">{value.error}</p>}
    {report && <>
      <p>Matched a saved decision: {report.covered_games} · No usable match: {report.uncovered_games}</p>
      {!report.covered_games && !report.uncovered_games && <p>No eligible imported games to compare.</p>}
      {!!report.covered_games && !report.deviations.length && <p>No departures found at covered decisions.</p>}
      {!!report.deviations.length && <ol aria-label="First departures by frequency">
        {report.deviations.map(move => <li key={`${move.node_id}:${move.uci}:${move.played_by}`}>
          {move.played_by === 'you' ? 'You' : 'Opponent'} played {move.san} in {move.games} {move.games === 1 ? 'game' : 'games'}.{' '}
          <Button disabled={blocked} onClick={() => practise(move.node_id)}>Practise before {move.san}</Button>
        </li>)}
      </ol>}
    </>}
  </section>;
}
