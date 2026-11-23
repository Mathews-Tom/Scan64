import { useEffect, useId, useState } from 'react';
import { feedbackIssueUrl, isDesktop, openFeedbackIssue, updateStatus } from '../platform';
import { Button, Panel, StatusLine } from './Primitives';

const KINDS = ['Bug', 'Idea', 'Question'] as const;
type Kind = typeof KINDS[number];
const MAX_SUMMARY = 100;
const MAX_DETAILS = 1500;

/** Keep only text the desktop shell accepts: newlines, no other control characters. */
function plainText(value: string): string {
  return value.replace(/\r\n?/g, '\n').replace(/\t/g, '  ').replace(/[^\P{Cc}\n]/gu, '');
}

function operatingSystem(userAgent: string): string {
  if (/Mac OS X|Macintosh/.test(userAgent)) return 'macOS';
  if (/Windows/.test(userAgent)) return 'Windows';
  if (/Linux/.test(userAgent)) return 'Linux';
  return 'unknown OS';
}

export function FeedbackPanel() {
  const desktop = isDesktop();
  const ids = { kind: useId(), summary: useId(), details: useId(), preview: useId() };
  const [kind, setKind] = useState<Kind>('Bug');
  const [summary, setSummary] = useState('');
  const [details, setDetails] = useState('');
  const [includeEnvironment, setIncludeEnvironment] = useState(true);
  // Browser installs have no shell-reported version; the server build is not identified here.
  const [version, setVersion] = useState<string | null>(desktop ? null : '');
  const [opening, setOpening] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!desktop) return;
    let current = true;
    void updateStatus()
      .then(status => { if (current) setVersion(status.installedVersion); })
      .catch(() => { if (current) setVersion('unknown version'); });
    return () => { current = false; };
  }, [desktop]);

  const os = operatingSystem(navigator.userAgent);
  const environment = desktop
    ? `Scan64 ${version ?? 'version loading'} · ${os} · desktop app`
    : `Scan64 · ${os} · browser`;
  const title = `${kind}: ${plainText(summary).replace(/\n/g, ' ').trim()}`;
  const body = [plainText(details).trim(), includeEnvironment ? `---\n${environment}` : '']
    .filter(Boolean).join('\n\n');
  const ready = summary.trim() !== '' && (!includeEnvironment || version !== null);

  async function open() {
    setError(null);
    setOpening(true);
    try {
      await openFeedbackIssue(title, body);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Cannot open the feedback form');
    } finally {
      setOpening(false);
    }
  }

  return (
    <Panel title="Send feedback" className="settings-feedback">
      <p>Report a bug or suggest an idea as a GitHub issue. Scan64 sends nothing itself: it opens GitHub's issue form in your browser with the text below, which you can still edit there. Submitting needs a GitHub account and makes the issue public. No games, player data or tokens are added; only what you type and, if you choose, the version line.</p>
      <label htmlFor={ids.kind}>Type</label>
      <select id={ids.kind} value={kind} onChange={event => setKind(event.target.value as Kind)}>
        {KINDS.map(option => <option key={option} value={option}>{option}</option>)}
      </select>
      <label htmlFor={ids.summary}>Summary</label>
      <input id={ids.summary} type="text" maxLength={MAX_SUMMARY} value={summary} onChange={event => setSummary(event.target.value)} />
      <label htmlFor={ids.details}>Details (optional)</label>
      <textarea id={ids.details} maxLength={MAX_DETAILS} rows={5} value={details} onChange={event => setDetails(event.target.value)} />
      <label>
        <input type="checkbox" checked={includeEnvironment} onChange={event => setIncludeEnvironment(event.target.checked)} />
        {' '}Include the version line: {environment}
      </label>
      <h3 id={ids.preview}>Issue preview</h3>
      <pre aria-labelledby={ids.preview} className="settings-feedback__preview">{`${title}\n\n${body}`}</pre>
      {desktop ? (
        <Button variant="primary" disabled={!ready || opening} onClick={() => { void open(); }}>
          {opening ? 'Opening GitHub…' : 'Open GitHub issue'}
        </Button>
      ) : ready ? (
        <a href={feedbackIssueUrl(title, body)} target="_blank" rel="noopener noreferrer">Open GitHub issue</a>
      ) : (
        <Button variant="primary" disabled>Open GitHub issue</Button>
      )}
      {!ready ? <p>Enter a summary to continue.</p> : null}
      {error ? <StatusLine kind="error">{error}</StatusLine> : null}
    </Panel>
  );
}
