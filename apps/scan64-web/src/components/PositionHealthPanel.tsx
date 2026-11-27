import { useEffect, useId, useState } from 'react';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import type { HealthMeterRead, PositionHealthRead } from '../api/types';
import { filledSegments, LEVEL_WORDS, meterText, SEGMENTS, TREND_MARKS } from '../healthPanel';
import './PositionHealthPanel.css';


function Meter({ name, side, meter, large = false }: { name: string; side: string; meter: HealthMeterRead; large?: boolean }) {
  const filled = filledSegments(meter.health);
  return <div className={large ? 'health-meter health-meter--overall' : 'health-meter'} data-level={meter.level} role="img" aria-label={`${side} ${meterText(name, meter)}`}>
    <span className="health-meter__track" aria-hidden="true">
      {Array.from({ length: SEGMENTS }, (_, index) =>
        <span key={index} className="health-meter__segment" data-on={index < filled ? 'true' : undefined}
          style={{ ['--segment' as string]: index / (SEGMENTS - 1) }} />)}
    </span>
    <span className="health-meter__word" aria-hidden="true">{LEVEL_WORDS[meter.level]}</span>
    <span className="health-meter__trend" data-trend={meter.trend} aria-hidden="true">{TREND_MARKS[meter.trend]}</span>
  </div>;
}

type State = { state: 'loading' } | { state: 'ready'; health: PositionHealthRead } | { state: 'error'; message: string };

export function PositionHealthPanel({ fen, previousFen }: { fen: string; previousFen: string | null }) {
  const [view, setView] = useState<State>({ state: 'loading' });
  const [details, setDetails] = useState(false);
  const detailsId = useId();

  useEffect(() => {
    let current = true;
    setView({ state: 'loading' });
    void (async () => {
      try {
        const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
        const health = await ApiClient.positionHealth({ player_id: playerId, fen, learner: 'white', previous_fen: previousFen });
        if (current) setView({ state: 'ready', health });
      } catch (error) {
        if (current) setView({ state: 'error', message: error instanceof Error ? error.message : 'Position health is unavailable.' });
      }
    })();
    return () => { current = false; };
  }, [fen, previousFen]);

  return <section className="working-controls__section position-health" data-testid="position-health" aria-label="Position health" aria-busy={view.state === 'loading'}>
    <h3>Position health</h3>
    {view.state === 'error' && <p className="position-health__note" data-testid="position-health-error">{view.message}</p>}
    {view.state === 'ready' && <>
      <table className="position-health__table">
        <thead><tr><th scope="col"><span className="visually-hidden">Factor</span></th><th scope="col">White</th><th scope="col">Black</th></tr></thead>
        <tbody>
          <tr className="position-health__overall">
            <th scope="row">overall</th>
            <td><Meter large name="overall" side="White" meter={view.health.overall.learner} /></td>
            <td><Meter large name="overall" side="Black" meter={view.health.overall.opponent} /></td>
          </tr>
          {view.health.groups.map(group => <tr key={group.key}>
            <th scope="row">{group.name}</th>
            <td><Meter name={group.name} side="White" meter={group.learner} /></td>
            <td><Meter name={group.name} side="Black" meter={group.opponent} /></td>
          </tr>)}
        </tbody>
      </table>
      <button type="button" className="position-health__details-toggle" aria-expanded={details} aria-controls={detailsId}
        onClick={() => setDetails(open => !open)}>{details ? 'Hide details' : 'Show details'}</button>
      {details && <dl id={detailsId} className="position-health__details">
        {[view.health.overall, ...view.health.groups].map(group => <div key={group.key}>
          <dt>{group.name}</dt>
          <dd>White: {terms(group.learner)}</dd>
          <dd>Black: {terms(group.opponent)}</dd>
        </div>)}
      </dl>}
      <p className="position-health__note">From Scan64's evaluation factors; no engine search or language model. The scale is provisional.</p>
    </>}
  </section>;
}

function terms(meter: HealthMeterRead): string {
  if (!meter.terms.length) return 'no change from the start';
  return meter.terms.map(term => `${term.name} ${term.pawns > 0 ? '+' : ''}${term.pawns.toFixed(2)}`).join(', ');
}
