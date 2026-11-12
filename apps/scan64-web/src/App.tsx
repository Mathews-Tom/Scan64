import { useCallback, useEffect, useState } from 'react';
import './App.css';
import { PlayScreen } from './components/PlayScreen';
import { PgnImportScreen } from './components/PgnImportScreen';
import { AnalysisScreen } from './components/AnalysisScreen';
import { FamousGameStudyScreen } from './components/FamousGameStudyScreen';
import { OpeningExplorerScreen } from './components/OpeningExplorerScreen';
import type { PlaySessionRead } from './api/types';
import { DailyTrainingScreen } from './components/DailyTrainingScreen';
import { ProfileScreen } from './components/ProfileScreen';
import { CoachDashboardScreen } from './components/CoachDashboardScreen';
import { GamesListScreen } from './components/GamesListScreen';
import { createActivePlayer, getStoredPlayerId } from './api/client';
import { TodayScreen } from './components/TodayScreen';

function decodeRouteSegment(value: string): string | null {
  try {
    const decoded = decodeURIComponent(value);
    return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(decoded)
      ? decoded
      : null;
  } catch {
    return null;
  }
}
function App() {
  const [pathname, setPathname] = useState(() => window.location.pathname);
  const [activePlaySession, setActivePlaySession] = useState<{session: PlaySessionRead, fen: string} | null>(null);
  const [activeAnalysisGameId, setActiveAnalysisGameId] = useState<string | undefined>();
  const [hasIdentity, setHasIdentity] = useState(() => getStoredPlayerId() !== null);
  const [displayName, setDisplayName] = useState('');
  const [creatingPlayer, setCreatingPlayer] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);

  useEffect(() => {
    const handlePopState = () => setPathname(window.location.pathname);
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  const navigate = useCallback((nextPathname: string) => {
    if (nextPathname === window.location.pathname) return;
    window.history.pushState({}, '', nextPathname);
    setPathname(window.location.pathname);
  }, []);
  const gameAnalysisMatch = /^\/games\/([^/]+)\/analysis$/.exec(pathname);
  const gameAnalysisId = gameAnalysisMatch ? decodeRouteSegment(gameAnalysisMatch[1]) : null;
  let screen = <div data-testid="not-found">Page not found</div>;
  const isKnownPath = gameAnalysisId !== null || [
    '/', '/play', '/import', '/famous', '/analysis', '/games',
    '/explorer', '/training', '/profile', '/coach', '/library',
  ].includes(pathname);
  if (gameAnalysisId !== null) {
    screen = (
      <AnalysisScreen
        key={gameAnalysisId}
        gameId={gameAnalysisId}
        onPlayFromHere={(session, fen) => {
          setActivePlaySession({ session, fen });
          navigate('/play');
        }}
      />
    );
  } else switch (pathname) {
    case '/':
      screen = <TodayScreen onNavigate={navigate} />;
      break;
    case '/play':
      screen = (
        <PlayScreen
          key={activePlaySession ? activePlaySession.session.id : 'new'}
          initialSession={activePlaySession?.session}
          initialFen={activePlaySession?.fen}
        />
      );
      break;
    case '/import':
      screen = (
        <PgnImportScreen
          onExploreAnalysis={(gameId) => {
            setActiveAnalysisGameId(gameId);
            navigate('/analysis');
          }}
        />
      );
      break;
    case '/famous':
      screen = (
        <FamousGameStudyScreen
          onPlayFromHere={(session, fen) => {
            setActivePlaySession({ session, fen });
            navigate('/play');
          }}
        />
      );
      break;
    case '/analysis':
      screen = (
        <AnalysisScreen
          key={activeAnalysisGameId ?? 'analysis'}
          gameId={activeAnalysisGameId}
          onPlayFromHere={(session, fen) => {
            setActivePlaySession({ session, fen });
            navigate('/play');
          }}
        />
      );
      break;
    case '/games':
      screen = <GamesListScreen onOpenGame={(gameId) => navigate(`/games/${encodeURIComponent(gameId)}/analysis`)} />;
      break;
    case '/explorer':
      screen = <OpeningExplorerScreen />;
      break;
    case '/training':
      screen = <DailyTrainingScreen />;
      break;
    case '/profile':
      screen = <ProfileScreen />;
      break;
    case '/coach':
      screen = <CoachDashboardScreen />;
      break;
    case '/library':
      screen = (
        <section className="library-screen">
          <h1>Library</h1>
          <p>Explore an opening, study a master game, or bring your own game to review.</p>
          <div className="library-links">
            <button onClick={() => navigate('/explorer')}>Opening Explorer</button>
            <button onClick={() => navigate('/famous')}>Famous Games</button>
            <button onClick={() => navigate('/import')}>Import PGN</button>
          </div>
        </section>
      );
      break;
  }

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <header className="app-header">
        <div className="app-brand"><span aria-hidden="true">♞</span> Scan64</div>
        <p>Your chess study table</p>
      </header>
      <nav className="app-nav" aria-label="Main navigation">
        {[
          ['Today', '/'], ['Play', '/play'], ['Review', '/games'],
          ['Train', '/training'], ['Profile', '/profile'],
          ['Library', '/library'], ['Coach', '/coach'],
        ].map(([label, path]) => {
          const selected = pathname === path
            || (path === '/games' && (pathname.startsWith('/games/') || pathname === '/analysis'))
            || (path === '/library' && ['/import', '/famous', '/explorer'].includes(pathname));
          return (
            <button
              key={path}
              aria-current={selected ? 'page' : undefined}
              disabled={!hasIdentity && isKnownPath}
              onClick={() => { if (path === '/play') setActivePlaySession(null); navigate(path); }}
            >{label}</button>
          );
        })}
      </nav>
      <main id="main-content">
        {!hasIdentity && isKnownPath ? (
          <section className="identity-screen" aria-labelledby="welcome-title">
            <h1 id="welcome-title">Welcome to Scan64</h1>
            <p>Play, revisit your games, and practise what matters. What should we call you?</p>
            <form onSubmit={(event) => {
              event.preventDefault();
              if (getStoredPlayerId() !== null) {
                setHasIdentity(true);
                return;
              }
              if (creatingPlayer || !displayName.trim()) return;
              setCreatingPlayer(true);
              setIdentityError(null);
              void createActivePlayer(displayName)
                .then(() => setHasIdentity(true))
                .catch((error: unknown) => setIdentityError(error instanceof Error ? error.message : 'Player creation failed'))
                .finally(() => setCreatingPlayer(false));
            }}>
              <label htmlFor="display-name">Display name</label>
              <input id="display-name" autoComplete="nickname" required value={displayName} onChange={(event) => setDisplayName(event.target.value)} />
              <button type="submit" disabled={creatingPlayer || !displayName.trim()}>
                {creatingPlayer ? 'Creating player…' : 'Start learning'}
              </button>
              {identityError ? <p role="alert">{identityError}. Please try again.</p> : null}
            </form>
            <p className="muted">Your player identity stays in this browser. No account or email needed.</p>
          </section>
        ) : (
          <>
            {['/games', '/analysis'].includes(pathname) || gameAnalysisId !== null ? (
              <nav className="section-nav" aria-label="Review navigation">
                <button onClick={() => navigate('/games')}>Your Games</button>
                <button onClick={() => navigate('/analysis')}>Analysis Board</button>
              </nav>
            ) : null}
            {screen}
          </>
        )}
      </main>
    </div>
  );
}

export default App;
