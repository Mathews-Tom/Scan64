import { useEffect, useRef, useState } from 'react';
import { RepertoireApi, type RepertoireAttemptInput, type RepertoireRead } from '../api/repertoire';
import { analysisEntries, analysisMoveLabel } from './analysisTree';
import { CoachBoard } from './CoachBoard';
import { Button, Panel } from './Primitives';

const MAX_PGN_BYTES = 256 * 1024;

export function RepertoireStudy() {
  const [repertoires, setRepertoires] = useState<RepertoireRead[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [nodeId, setNodeId] = useState(0);
  const [pgn, setPgn] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [drilling, setDrilling] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [retry, setRetry] = useState<RepertoireAttemptInput | null>(null);
  const pending = useRef(false);
  const selected = repertoires.find(item => item.id === selectedId);
  const node = selected?.tree.nodes[nodeId];
  const decision = selected?.decisions.find(item => item.node_id === nodeId);

  useEffect(() => {
    let active = true;
    void RepertoireApi.list().then(items => {
      if (active) setRepertoires(existing => [
        ...items.filter(item => !existing.some(saved => saved.id === item.id)), ...existing,
      ]);
    }).catch(caught => {
      if (active) setError(caught instanceof Error ? caught.message : 'Could not load repertoires.');
    }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const select = (id: string, position = 0, drill = false) => {
    setSelectedId(id);
    setNodeId(position);
    setDrilling(drill);
    setStatus('');
    setError('');
  };

  const importPgn = async () => {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    try {
      const imported = await RepertoireApi.importPgn(pgn);
      setRepertoires(items => [...items, ...imported]);
      select(imported[0].id);
      setStatus(`Imported ${imported.length} repertoire record${imported.length === 1 ? '' : 's'} with all variations.`);
      setPgn('');
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not import repertoire.');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  const submit = async (attempt: RepertoireAttemptInput) => {
    if (!selected || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError('');
    setRetry(attempt);
    try {
      const result = await RepertoireApi.review(selected.id, attempt);
      setRepertoires(items => items.map(item => item.id === selected.id ? result.repertoire : item));
      setStatus(`${result.success ? 'Saved continuation — reviewed.' : 'Legal move, but not a saved continuation — Again.'} Next due ${new Date(result.next_review_at).toLocaleString()}.`);
      setRetry(null);
      setDrilling(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Could not record review. Retry the same submission.');
    } finally {
      pending.current = false;
      setBusy(false);
    }
  };

  const blocked = busy || retry !== null;
  return <section aria-label="My repertoires">
    <h3>My repertoires</h3>
    <p>Import your own standard-chess PGN. Every record and nested variation is retained. Practise either side to move; no engine answers, ratings or awards.</p>
    <label htmlFor="repertoire-pgn">Repertoire PGN</label>
    <textarea id="repertoire-pgn" value={pgn} onChange={event => setPgn(event.target.value)} maxLength={MAX_PGN_BYTES} disabled={blocked} />
    <label htmlFor="repertoire-file">Choose repertoire PGN file (up to 256 KiB)</label>
    <input id="repertoire-file" type="file" accept=".pgn,text/plain,application/x-chess-pgn" disabled={blocked} onChange={event => {
      const file = event.target.files?.[0];
      if (!file) return;
      if (file.size > MAX_PGN_BYTES) { setError('Repertoire PGN must be at most 256 KiB.'); return; }
      void file.text().then(text => { setPgn(text); setError(''); }).catch(() => setError('Could not read PGN file.'));
    }} />
    <Button disabled={blocked || !pgn.trim()} onClick={() => void importPgn()}>Import repertoire</Button>
    {loading && <p role="status">Loading repertoires…</p>}
    {error && <p role="alert">{error}</p>}
    {status && <p role="status">{status}</p>}
    {retry && !busy && <Button onClick={() => void submit(retry)}>Retry same submission</Button>}
    <ul aria-label="Saved repertoires">
      {repertoires.map(item => <li key={item.id}>
        <Button disabled={blocked} aria-pressed={selectedId === item.id} onClick={() => select(item.id)}>{item.title}</Button>
        <span> {item.reviewed_decisions} / {item.total_decisions} decisions reviewed · {item.due_decisions} due</span>
      </li>)}
    </ul>
    {selected && node && <Panel aria-label="Saved repertoire playback">
      <h4>{selected.title}</h4>
      <p>Coverage: {selected.reviewed_decisions} / {selected.total_decisions} saved decision positions reviewed at least once (including Again). Leaves are not decisions. Due: {selected.due_decisions}; due includes unreviewed positions.</p>
      <p>{node.fen.split(' ')[1] === 'w' ? 'White' : 'Black'} to move. {decision ? `${decision.reviewed ? 'Reviewed' : 'Not reviewed'} · ${decision.due ? 'Due now' : `Next due ${new Date(decision.next_review_at!).toLocaleString()}`}` : 'End of saved line — no drill here.'}</p>
      <CoachBoard key={`${selected.id}:${nodeId}:${drilling}`} fen={node.fen}
        orientation={node.fen.split(' ')[1] === 'w' ? 'white' : 'black'} flippable
        disabled={!drilling || blocked} deferMoveToParent resetAfterAttempt
        testId="repertoire-board"
        onMove={uci => submit({ attempt_id: crypto.randomUUID(), node_id: nodeId, submitted_move: uci })} />
      {drilling ? <p>Play one saved continuation. A legal different move is recorded as Again; an illegal move is not a review.</p> : <Button disabled={blocked || !decision} onClick={() => { setDrilling(true); setStatus(''); }}>Practise this position</Button>}
      <Button disabled={blocked || !selected.due_decisions} onClick={() => {
        const due = selected.decisions.find(item => item.due);
        if (due) select(selected.id, due.node_id, true);
      }}>Practise next due</Button>
      <div role="group" aria-label="Repertoire navigation">
        <Button disabled={blocked || node.parent === null} onClick={() => select(selected.id, 0)}>First position</Button>
        <Button disabled={blocked || node.parent === null} onClick={() => select(selected.id, node.parent!)}>Previous position</Button>
        <Button disabled={blocked || !node.children.length} onClick={() => select(selected.id, node.children[0])}>Next position</Button>
      </div>
      {!drilling && <ol aria-label="Repertoire variations">
        {analysisEntries(selected.tree).map(({ id, depth }) => <li key={id} style={{ paddingInlineStart: `${Math.min(depth, 4) * 0.75}rem` }}>
          <Button disabled={blocked} aria-current={nodeId === id ? 'step' : undefined} onClick={() => select(selected.id, id)}>
            {depth ? 'Variation: ' : ''}{analysisMoveLabel(selected.tree, id)}
          </Button>
        </li>)}
      </ol>}
    </Panel>}
  </section>;
}
