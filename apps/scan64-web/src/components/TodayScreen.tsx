import { useEffect, useState } from 'react';
import { ApiClient, ensurePlayerAuthorization, getActivePlayerId } from '../api/client';
import type { PatternsReport, PlayerGamesPage, PuzzleDailyRead, WeeklyReport } from '../api/types';

interface TodayData {
  games: PlayerGamesPage;
  weekly: WeeklyReport;
  patterns: PatternsReport;
  puzzles: PuzzleDailyRead;
}

export function TodayScreen({ onNavigate }: { onNavigate: (path: string) => void }) {
  const [data, setData] = useState<TodayData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    let cancelled = false;
    void ensurePlayerAuthorization(getActivePlayerId())
      .then(async (playerId) => {
        const [games, weekly, patterns, puzzles] = await Promise.all([
          ApiClient.getPlayerGames(playerId),
          ApiClient.getWeeklyReport(playerId),
          ApiClient.getPlayerPatterns(playerId),
          ApiClient.getPuzzleDaily(),
        ]);
        if (!cancelled) setData({ games, weekly, patterns, puzzles });
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : 'Reports unavailable');
      });
    return () => { cancelled = true; };
  }, [request]);

  const latestGame = data?.games.items.find(game => game.diagnosis_count > 0);
  const weakestSkills = [...(data?.weekly.active_mastery ?? [])].sort((a, b) => a.mastery - b.mastery).slice(0, 3);

  return (
    <section className="today-screen" aria-labelledby="today-title">
      <header className="today-heading">
        <h1 id="today-title">Today</h1>
        <p>A game to play. A moment to understand. A skill to practise.</p>
      </header>
      <div className="today-actions" role="group" aria-label="Choose your next step">
        <button onClick={() => onNavigate('/play')}><strong>Play</strong><span>Put your ideas on the board</span></button>
        <button onClick={() => onNavigate('/training')}><strong>Train</strong><span>Practise with a daily lesson</span></button>
        <button onClick={() => onNavigate('/games')}><strong>Review</strong><span>Learn from your own games</span></button>
      </div>
      {error ? (
        <div role="alert" className="report-error">
          <p>Today’s reports could not load: {error}</p>
          <button onClick={() => { setError(null); setRequest(request + 1); }}>Try again</button>
        </div>
      ) : !data ? <p role="status">Loading your study table…</p> : (
        <div className="today-panels">
          <section className="study-panel">
            <h2>Your puzzle practice</h2>
            <p>Scan64 puzzle rating: {data.puzzles.rating}{data.puzzles.provisional ? ' (provisional)' : ''} · {data.puzzles.rated_count} rated puzzles</p>
            <p>{data.puzzles.completed} of {data.puzzles.total} completed · {data.puzzles.remaining} remaining · {data.puzzles.overdue_count} overdue reviews</p>
            <p>{data.puzzles.message}</p>
            <p>A separate practice rating, not chess strength or theme mastery. Daily sets reset at UTC midnight.</p>
            <a href="/puzzles" onClick={(event) => {
              if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              onNavigate('/puzzles');
            }}>Continue puzzles</a>
          </section>
          <section className="study-panel">
            <h2>Your latest analysed game</h2>
            {latestGame ? (
              <>
                <p>{latestGame.white} vs {latestGame.black}</p>
                <p className="muted">{latestGame.result} · {latestGame.diagnosis_count} {latestGame.diagnosis_count === 1 ? 'lesson' : 'lessons'}</p>
                <a href={`/games/${encodeURIComponent(latestGame.id)}/analysis`} onClick={(event) => {
                  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
                  event.preventDefault();
                  onNavigate(`/games/${encodeURIComponent(latestGame.id)}/analysis`);
                }}>Review this game</a>
              </>
            ) : <p>No reviewed mistakes to show yet. Analyse a game from Review, or play or import a game.</p>}
          </section>
          <section className="study-panel">
            <h2>Skills to practise</h2>
            {weakestSkills.length ? (
              <ul>{weakestSkills.map(skill => <li key={skill.concept_code}>{skill.name}</li>)}</ul>
            ) : <p>Analyse a game to discover which skills need attention.</p>}
          </section>
          <section className="study-panel">
            <h2>This week</h2>
            <p>{data.weekly.games_played} {data.weekly.games_played === 1 ? 'game' : 'games'} played in the last 7 days.</p>
            <p>{data.weekly.active_concepts_observed} {data.weekly.active_concepts_observed === 1 ? 'skill' : 'skills'} observed.</p>
          </section>
          <section className="study-panel">
            <h2>Patterns in your play</h2>
            {data.patterns.recurring_diagnoses.length ? (
              <ul>{data.patterns.recurring_diagnoses.map(pattern => (
                <li key={pattern.diagnosis}>{pattern.name}: {pattern.occurrence_count} games</li>
              ))}</ul>
            ) : <p>{data.patterns.status === 'insufficient_data'
              ? 'More analysed games are needed to spot recurring patterns.'
              : 'No recurring pattern found yet. Keep reviewing your games.'}</p>}
          </section>
        </div>
      )}
    </section>
  );
}
