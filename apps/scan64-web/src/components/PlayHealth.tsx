import { useEffect, useRef, useState } from 'react';
import { ApiClient } from '../api/client';
import type { AssistanceMode } from '../api/types';
import { readPlayHealth, readPlayHealthCounts, savePlayHealth, savePlayHealthCounts } from '../healthPanel';
import { PositionHealthPanel } from './PositionHealthPanel';
import { Panel, StatusLine } from './Primitives';

/**
 * Health meters during play (#417): a level of help below the first hint.
 * The meters show factor health and its change, never a move or a square.
 * Reading them counts as the first hint level only when the learner turns
 * that on; then each position they are shown at, with the learner to move,
 * is recorded once.
 */
export function PlayHealth({ playerId, sessionId, fen, previousFen, learner, learnerToMove, onAssistance }: {
  playerId: string;
  sessionId: string;
  fen: string;
  previousFen: string | null;
  learner: 'white' | 'black' | undefined;
  learnerToMove: boolean;
  onAssistance: (modes: AssistanceMode[]) => void;
}) {
  const [shown, setShown] = useState(readPlayHealth);
  const [counted, setCounted] = useState(readPlayHealthCounts);
  const [saveError, setSaveError] = useState(false);
  const [recordError, setRecordError] = useState<string | null>(null);
  const recorded = useRef(new Set<string>());

  useEffect(() => {
    if (!shown || !counted || !learnerToMove) return;
    const key = `${sessionId}:${fen}`;
    if (recorded.current.has(key)) return;
    recorded.current.add(key);
    setRecordError(null);
    ApiClient.recordHealthHint(sessionId, playerId, fen)
      .then(read => onAssistance(read.assistance_modes))
      .catch((error: unknown) => {
        recorded.current.delete(key);
        setRecordError(error instanceof Error ? error.message : 'Could not record the health reading as a hint.');
      });
  }, [shown, counted, learnerToMove, sessionId, playerId, fen, onAssistance]);

  return <Panel title="Health meters" aria-label="Health meters">
    <label><input type="checkbox" checked={shown} data-testid="play-health-setting" onChange={event => {
      setShown(event.target.checked);
      setSaveError(!savePlayHealth(event.target.checked));
    }} /> Show health meters</label>
    <p>Subtler than a hint: how each side's king safety, material and other factors stand, and whether they rose or fell. The meters never name a move or a square.</p>
    <label><input type="checkbox" checked={counted} data-testid="play-health-counts" onChange={event => {
      setCounted(event.target.checked);
      setSaveError(!savePlayHealthCounts(event.target.checked));
    }} /> Count the meters as a hint</label>
    <p>Off by default: the meters do not count as a hint. When on, every position you see them on your move counts as the first hint level.</p>
    {saveError ? <StatusLine kind="error">Changed for this session. Your browser did not allow saving the preference.</StatusLine> : null}
    {recordError ? <StatusLine kind="error">{recordError}</StatusLine> : null}
    {shown && <PositionHealthPanel fen={fen} previousFen={previousFen} learner={learner} />}
  </Panel>;
}
