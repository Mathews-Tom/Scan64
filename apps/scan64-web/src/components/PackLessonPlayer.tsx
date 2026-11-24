import { useRef, useState } from 'react';
import { LessonPacksApi, type LessonRead, type PackCheckRead } from '../api/lessonPacks';
import { CoachBoard } from './CoachBoard';
import { Button, Panel } from './Primitives';

export function PackLessonPlayer({ lesson, onClose }: { lesson: LessonRead; onClose: () => void }) {
  const [position, setPosition] = useState<PackCheckRead>({ accepted: false, fen: lesson.fen, ply: 0, reply: null, complete: false, hints: [], explanation: null });
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [message, setMessage] = useState('');
  async function check(input: { move?: string; hint?: boolean }) {
    if (locked.current || position.complete) return;
    locked.current = true;
    setBusy(true);
    setMessage('');
    try {
      const result = await LessonPacksApi.check(lesson.pack_id, lesson.id, { ply: position.ply, ...input });
      setPosition(result);
      if (input.move) setMessage(result.accepted ? result.complete ? 'Lesson complete.' : 'Correct. Continue from the new position.' : 'That is not the pack continuation. Try again.');
    } catch (error) { setMessage(error instanceof Error ? error.message : 'Could not check the move. Try again.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <Panel title={lesson.title}>
    <p>Community pack: {lesson.pack_title} · Author: {lesson.author}</p>
    <p>This lesson is not from your games. Playing it does not change progress, skills or diagnosis.</p>
    <p><strong>Objective:</strong> {lesson.objective}</p>
    <CoachBoard fen={position.fen} orientation={lesson.fen.split(' ')[1] === 'b' ? 'black' : 'white'}
      deferMoveToParent resetAfterAttempt disabled={busy || position.complete} onMove={move => check({ move })} />
    <p role="status">{message}</p>
    <Button disabled={busy || position.complete} onClick={() => { void check({ hint: true }); }}>Show hint</Button>
    {position.hints.length > 0 && <ul aria-label="Lesson hints">{position.hints.map((hint, index) => <li key={index}>{hint}</li>)}</ul>}
    {position.complete && position.explanation !== null && <section aria-label="Lesson explanation"><h3>Explanation</h3><p>{position.explanation}</p></section>}
    {lesson.themes.length > 0 && <p>Themes: {lesson.themes.join(', ')}</p>}
    <Button onClick={onClose}>Back to lesson packs</Button>
  </Panel>;
}
