import { useEffect, useRef, useState } from 'react';
import { LessonPacksApi, MAX_PACK_BYTES, type LessonRead, type PackRead } from '../api/lessonPacks';
import { Button, Panel } from './Primitives';
import { PackLessonPlayer } from './PackLessonPlayer';

export function LessonPacksPanel() {
  const [packs, setPacks] = useState<PackRead[]>([]);
  const [lesson, setLesson] = useState<LessonRead | null>(null);
  const [busy, setBusy] = useState(false);
  const locked = useRef(false);
  const [error, setError] = useState('');
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() {
      try {
        const result = await LessonPacksApi.list();
        if (!active) return;
        setPacks(result);
        if (result.some(pack => pack.state === 'pending' || pack.state === 'verifying')) timer = setTimeout(() => { void load(); }, 1500);
      } catch (cause) { if (active) setError(cause instanceof Error ? cause.message : 'Could not load lesson packs.'); }
    }
    void load();
    return () => { active = false; clearTimeout(timer); };
  }, [refresh]);
  async function act(action: () => Promise<unknown>, reload = true) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try { await action(); if (reload) setRefresh(value => value + 1); }
    catch (cause) { setError(cause instanceof Error ? cause.message : 'Lesson pack request failed.'); }
    finally { locked.current = false; setBusy(false); }
  }
  return <Panel title="Community lesson packs">
    <p>Install a local JSON file. Each lesson is verified on this device before play. Packs are not from your games and never change progress, skills or diagnosis.</p>
    <label>Lesson pack JSON file <input type="file" accept=".json,application/json" disabled={busy} onChange={event => {
      const file = event.target.files?.[0];
      event.target.value = '';
      if (!file) return;
      if (file.size > MAX_PACK_BYTES) { setError('Lesson pack files must be at most 512 KiB.'); return; }
      void act(() => LessonPacksApi.install(file));
    }} /></label>
    {error && <p role="alert">{error}</p>}
    <Button disabled={busy} onClick={() => setRefresh(value => value + 1)}>Refresh lesson packs</Button>
    {packs.length === 0 && <p>No community packs installed.</p>}
    <ul aria-label="Installed lesson packs">{packs.map(pack => <li key={pack.id}>
      <h3>{pack.title}</h3>
      <p>Author: {pack.author} · Licence: {pack.licence} · Version: {pack.version}</p>
      <p title={pack.sha256}>SHA-256: {pack.sha256.slice(0, 12)} · Installed: {new Date(pack.installed_at).toLocaleString()}</p>
      {pack.description && <p>{pack.description}</p>}{pack.source && <p>Source: {pack.source}</p>}
      <p role="status">{pack.state === 'unavailable' ? 'Verification unavailable' : pack.state}{pack.reason ? `: ${pack.reason}` : ''}</p>
      {pack.state !== 'unavailable' && <ul aria-label={`${pack.title} lessons`}>{pack.lessons.map(item => <li key={item.id}>
        <span>{item.title} — {item.state}{item.failing_ply !== null ? ` (ply ${item.failing_ply})` : ''}{item.reason ? `: ${item.reason}` : ''}</span>{' '}
        {pack.state === 'verified' && item.state === 'verified' && <Button disabled={busy} onClick={() => { void act(async () => setLesson(await LessonPacksApi.lesson(pack.id, item.id)), false); }}>Open {item.title}</Button>}
      </li>)}</ul>}
      {(pack.state === 'failed' || pack.state === 'unavailable') && <Button disabled={busy} onClick={() => { void act(() => LessonPacksApi.retry(pack.id)); }}>Retry {pack.title}</Button>}
      {confirmId === pack.id ? <div role="group" aria-label={`Confirm uninstall ${pack.title}`}>
        <p>Uninstall {pack.title} and all its lessons? Keep your original file to reinstall and verify again.</p>
        <Button disabled={busy} onClick={() => { void act(async () => { await LessonPacksApi.uninstall(pack.id); setConfirmId(null); if (lesson?.pack_id === pack.id) setLesson(null); }); }}>Confirm uninstall {pack.title}</Button>
        <Button disabled={busy} onClick={() => setConfirmId(null)}>Cancel uninstall</Button>
      </div> : <Button disabled={busy} onClick={() => setConfirmId(pack.id)}>Uninstall {pack.title}</Button>}
    </li>)}</ul>
    {lesson && <PackLessonPlayer key={`${lesson.pack_id}/${lesson.id}`} lesson={lesson} onClose={() => setLesson(null)} />}
  </Panel>;
}
