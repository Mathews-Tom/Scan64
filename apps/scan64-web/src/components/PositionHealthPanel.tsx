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

const OUTCOME_TEXT: Record<NonNullable<PositionHealthRead['outcome']>, string> = {
  checkmate: 'Checkmate',
  stalemate: 'Stalemate',
  insufficient_material: 'Draw by insufficient material',
  seventyfive_moves: 'Draw by the 75-move rule',
};

// learner: the side the panel speaks for (You / Opponent); without one it reads White / Black.
export function PositionHealthPanel({ fen, previousFen, learner }: { fen: string; previousFen: string | null; learner?: 'white' | 'black' }) {
  const [first, second] = learner === undefined ? ['White', 'Black'] : ['You', 'Opponent'];
  const [view, setView] = useState<State>({ state: 'loading' });
  // The last reading stays on screen while the next one loads, so the panel keeps its height.
  const [busy, setBusy] = useState(true);
  const [details, setDetails] = useState(false);
  const detailsId = useId();

  useEffect(() => {
    // Stepping through moves cancels the request for the position left behind.
    const request = new AbortController();
    setBusy(true);
    void (async () => {
      try {
        const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
        const health = await ApiClient.positionHealth(
          { player_id: playerId, fen, learner: learner ?? 'white', previous_fen: previousFen }, request.signal);
        if (!request.signal.aborted) { setView({ state: 'ready', health }); setBusy(false); }
      } catch (error) {
        if (!request.signal.aborted) {
          setView({ state: 'error', message: error instanceof Error ? error.message : 'Position health is unavailable.' });
          setBusy(false);
        }
      }
    })();
    return () => { request.abort(); };
  }, [fen, previousFen, learner]);

  return <section className="working-controls__section position-health" data-testid="position-health" aria-label="Position health" aria-busy={busy}>
    <h3>Position health<span className="position-health__updating" aria-hidden="true">Updating</span></h3>
    {view.state === 'error' && <p className="position-health__note" data-testid="position-health-error">{view.message}</p>}
    {view.state === 'ready' && view.health.outcome !== null &&
      <p className="position-health__note" data-testid="position-health-outcome">{OUTCOME_TEXT[view.health.outcome]}: the game is over, so there is no position health to read.</p>}
    {view.state === 'ready' && view.health.outcome === null && <>
      <table className="position-health__table">
        <colgroup><col className="position-health__name" /><col /><col /></colgroup>
        <thead><tr><th scope="col"><span className="visually-hidden">Factor</span></th><th scope="col">{first}</th><th scope="col">{second}</th></tr></thead>
        <tbody>
          <tr className="position-health__overall">
            <th scope="row">overall</th>
            <td><Meter large name="overall" side={first} meter={view.health.overall.learner} /></td>
            <td><Meter large name="overall" side={second} meter={view.health.overall.opponent} /></td>
          </tr>
          {view.health.groups.map(group => <tr key={group.key}>
            <th scope="row">{group.name}</th>
            <td><Meter name={group.name} side={first} meter={group.learner} /></td>
            <td><Meter name={group.name} side={second} meter={group.opponent} /></td>
          </tr>)}
        </tbody>
      </table>
      <button type="button" className="position-health__details-toggle" aria-expanded={details} aria-controls={detailsId}
        onClick={() => setDetails(open => !open)}>{details ? 'Hide details' : 'Show details'}</button>
      {details && <dl id={detailsId} className="position-health__details">
        {[view.health.overall, ...view.health.groups].map(group => <div key={group.key}>
          <dt>{group.name}</dt>
          <dd>{first}: {terms(group.learner)}</dd>
          <dd>{second}: {terms(group.opponent)}</dd>
        </div>)}
      </dl>}
      <p className="position-health__note">From Scan64's evaluation factors; no engine search or language model.</p>
    </>}
  </section>;
}

function terms(meter: HealthMeterRead): string {
  if (!meter.terms.length) return 'no nonzero terms';
  return meter.terms.map(term => `${term.name} ${term.pawns > 0 ? '+' : ''}${term.pawns.toFixed(2)}`).join(', ');
}
