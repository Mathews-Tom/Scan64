import { useState } from 'react';
import type { ReactNode } from 'react';
import { ApiClient } from '../api/client';
import type { PatternsReport } from '../api/types';

export function PatternCard({ report, owner = true, onNavigate, children }: {
  report: PatternsReport;
  owner?: boolean;
  onNavigate?: (path: string) => void;
  children?: ReactNode;
}) {
  const pattern = report.recurring_diagnoses[0];
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState('');
  const [dailyFallback, setDailyFallback] = useState(false);
  const practice = async () => {
    setPending(true); setMessage(''); setDailyFallback(false);
    try {
      let offset = 0;
      while (true) {
        const page = await ApiClient.getPuzzleMistakes(offset);
        const item = page.items.find(item => item.diagnosis === pattern?.diagnosis && item.attempts_remaining > 0);
        if (item) {
          const query = new URLSearchParams({ lesson: item.lesson_id, game: item.game_id });
          for (const theme of pattern?.practice_themes ?? []) query.append('fallbackTheme', theme);
          const path = `/puzzles?${query}`;
          if (onNavigate) onNavigate(path); else window.location.assign(path);
          return;
        }
        if (!page.has_more) break;
        offset = page.offset + page.limit;
      }
      const modes = await ApiClient.getPuzzleModes();
      const theme = pattern?.practice_themes?.find(value => modes.themes.includes(value));
      if (theme) {
        const path = `/puzzles?theme=${encodeURIComponent(theme)}`;
        if (onNavigate) onNavigate(path); else window.location.assign(path);
        return;
      }
      setMessage('No eligible same-pattern retries or mapped theme supply is available.');
      setDailyFallback(true);
    } catch (failure) {
      setMessage(`Related practice could not be loaded: ${failure instanceof Error ? failure.message : 'Please try again.'}`);
      setDailyFallback(true);
    } finally { setPending(false); }
  };
  return <section className="study-panel" aria-label="Recurring pattern">
    <h2>Recurring pattern</h2>
    <p>{report.eligible_game_count} eligible analysed games · recurrence requires {report.minimum_occurrences} distinct games.</p>
    {report.status === 'insufficient_data' ? <p>More eligible analysed games are needed before recurrence can be assessed.</p>
      : !pattern ? <p>No trusted diagnosis recurs across {report.minimum_occurrences} distinct games.</p>
        : <>
          <h3>{pattern.name}</h3>
          <p>{pattern.template}</p>
          <p>{pattern.observed_loss === null ? 'Observed loss unavailable: this recurring pattern has no scored retained moments.'
            : `Highest observed loss among scored retained moments: ${(pattern.observed_loss * 100).toFixed(1)} percentage points of summed win-probability loss.`}</p>
          <p>Support: {pattern.occurrence_count} distinct games · Coverage: {pattern.scored_occurrences}/{pattern.total_occurrences} scored retained moments.</p>
          {owner ? <>
            <ul>{pattern.supporting_moves?.map(move => <li key={`${move.game_id}-${move.ply}`}>
              <a href={`/games/${encodeURIComponent(move.game_id)}/analysis?ply=${move.ply}`}>Game {move.game_id}, ply {move.ply}</a>
              {move.loss === null ? ' · unscored' : ` · ${(move.loss * 100).toFixed(1)} percentage points loss`}
            </li>)}</ul>
            <button disabled={pending} onClick={() => void practice()}>{pending ? 'Finding related practice…' : 'Start pattern practice'}</button>
            <p>Try eligible same-pattern Your mistakes first, then related unrated Theme practice. Themes do not measure diagnosis mastery; existing attempt limits apply.</p>
            {message ? <p role="status">{message}</p> : null}
            {dailyFallback ? <a href="/puzzles">Daily puzzles — may be unrelated</a> : null}
            {children}
          </> : null}
        </>}
    <p className="muted">Only trusted, visible retained lessons count. Retention limits and missing scores leave gaps: this is not all errors, a causal explanation, statistical significance, or proof of a habit or cure. Missing loss is unavailable, not zero. Review explanations and retries are limited to eligible key moments.</p>
  </section>;
}
