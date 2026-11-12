import { useEffect, useRef, useState } from 'react';
import { linePosition } from './linePosition';
import { ApiClient } from '../api/client';
import type { MoveInsight, PositionInsight, Visualization } from '../api/types';

export interface HintPreview { sourceFen: string; fen: string; visualizations: Visualization[] }

export function HintPanel({ playerId, sessionId, fen, disabled = false, onPreview }: {
  playerId: string; sessionId?: string; fen: string; disabled?: boolean;
  onPreview: (preview: HintPreview | null) => void;
}) {
  const [result, setResult] = useState<PositionInsight | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selection, setSelection] = useState<{ candidate: number; step: number } | null>(null);
  const generation = useRef(0);
  useEffect(() => {
    generation.current += 1;
    setResult(null); setSelection(null); setError(null); setBusy(false); onPreview(null);
    return () => { generation.current += 1; };
  }, [fen, sessionId, playerId, disabled, onPreview]);
  const arrows = (insight: MoveInsight): Visualization[] => {
    const visuals = insight.visualizations.filter(v => v.command !== 'draw_arrow');
    insight.candidates.slice(0, 3).forEach((candidate, index) => {
      visuals.push({ command: 'draw_arrow', description: candidate.san,
        origin: candidate.move.slice(0, 2), target: candidate.move.slice(2, 4), label: String(index + 1) });
    });
    return visuals;
  };
  const request = async () => {
    const requestGeneration = ++generation.current;
    const step = Math.min((result?.step ?? 0) + 1, 3) as 1 | 2 | 3;
    setBusy(true); setError(null);
    try {
      const insight = await ApiClient.getPositionInsight(sessionId
        ? { player_id: playerId, play_session_id: sessionId, step } : { player_id: playerId, fen, step });
      if (requestGeneration !== generation.current) return;
      if (insight.step === 3 && insight.fen !== fen) { setError('The game position changed. Request a new hint.'); return; }
      setResult(insight); setSelection(null);
      onPreview({ sourceFen: fen, fen, visualizations: insight.step === 3 ? arrows(insight) : insight.visualizations });
    } catch (failure) {
      if (requestGeneration === generation.current) setError(failure instanceof Error ? failure.message : 'Hint unavailable');
    } finally { if (requestGeneration === generation.current) setBusy(false); }
  };
  const preview = (candidate: number, step: number) => {
    if (!result || result.step !== 3) return;
    try {
      const item = result.candidates[candidate];
      const previewFen = linePosition(result.fen, item.line, step);
      setSelection({ candidate, step });
      onPreview({ sourceFen: fen, fen: previewFen, visualizations: step ? item.visualizations.filter(v => v.command !== 'draw_arrow') : arrows(result) });
    } catch { setError('This candidate line cannot be previewed.'); }
  };
  return <section aria-label="Position hints">
    <button type="button" disabled={disabled || busy || result?.step === 3} onClick={() => void request()}>{busy ? 'Finding hints…' : 'Hint'}</button>
    {error && <p role="alert">{error}</p>}
    {result && !disabled && <div>
      <p>Hint {result.step} of 3</p>
      {result.step !== 3 ? <p>{result.text}</p> : <>
      <p>Explore a line without playing a move.</p>
      <ol>{result.candidates.slice(0, 3).map((candidate, index) => <li key={candidate.move}>
        <strong>{index + 1}. {candidate.san}</strong>
        {candidate.reasons.length ? <ul>{candidate.reasons.map((reason, i) => <li key={i}><strong>{reason.tier === 'principle' ? 'Principle' : 'Tactical'}:</strong> {reason.description}</li>)}</ul> : <p>engine preference</p>}
        <button type="button" onClick={() => preview(index, 1)}>Preview candidate {index + 1}</button>
        <button type="button" onClick={() => preview(index, candidate.line.length)}>Full line {index + 1}</button>
        {selection?.candidate === index && <>
          <span role="status">Step {selection.step} of {candidate.line.length}</span>
          <button type="button" disabled={!selection.step} onClick={() => preview(index, selection.step - 1)}>Previous step</button>
          <button type="button" disabled={selection.step >= candidate.line.length} onClick={() => preview(index, selection.step + 1)}>Next step</button>
        </>}
      </li>)}</ol>
      <button type="button" onClick={() => { setSelection(null); onPreview({ sourceFen: fen, fen, visualizations: arrows(result) }); }}>Return to game position</button>
      </>}
      <button type="button" onClick={() => { setResult(null); setSelection(null); onPreview(null); }}>Close hints</button>
    </div>}
  </section>;
}
