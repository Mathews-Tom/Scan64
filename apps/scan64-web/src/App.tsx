import { PuzzleImportProvider, PuzzleImportFoot } from './components/PuzzleImport';
import { DesktopUpdateBanner } from './components/DesktopUpdateBanner';
import { useCallback, useEffect, useRef, useState } from 'react';
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
import { PuzzlesScreen } from './components/PuzzlesScreen';
import { SettingsScreen, initializeAppearance } from './components/SettingsScreen';
import { NavigationIcon } from './components/NavigationIcon';
import { Button, Panel } from './components/Primitives';
import './components/WorkingScreens.css';

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
  useState(initializeAppearance);
  const [puzzleLibraryRevision, setPuzzleLibraryRevision] = useState(0);
  const [pathname, setPathname] = useState(() => window.location.pathname);
  const [activePlaySession, setActivePlaySession] = useState<{session: PlaySessionRead, fen: string} | null>(null);
  const [activeAnalysisGameId, setActiveAnalysisGameId] = useState<string | undefined>();
  const [hasIdentity, setHasIdentity] = useState(() => getStoredPlayerId() !== null);
  const [displayName, setDisplayName] = useState('');
  const [creatingPlayer, setCreatingPlayer] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);
  const mainRef = useRef<HTMLElement>(null);
  const previousScreen = useRef({ pathname, hasIdentity });
  useEffect(() => {
    const titles: Record<string, string> = {
      '/': 'Today', '/play': 'Play', '/games': 'Your games', '/analysis': 'Analysis',
      '/training': 'Daily Training', '/puzzles': 'Puzzles', '/profile': 'Profile',
      '/coach': 'Coach', '/library': 'Library', '/import': 'Import PGN',
      '/famous': 'Famous Games', '/explorer': 'Opening Explorer', '/settings': 'Settings',
    };
    document.title = `${!hasIdentity && pathname !== '/settings' ? 'Welcome' : titles[pathname] ?? (pathname.startsWith('/games/') ? 'Game review' : 'Page not found')} · Scan64`;
    if (previousScreen.current.pathname !== pathname || previousScreen.current.hasIdentity !== hasIdentity) {
      mainRef.current?.focus();
    }
    previousScreen.current = { pathname, hasIdentity };
  }, [pathname, hasIdentity]);

  useEffect(() => {
    const handlePopState = () => {
      if (window.location.pathname !== '/play') setActivePlaySession(null);
      setPathname(window.location.pathname);
    };
    const handleStorage = () => {
      if (getStoredPlayerId() !== null) setHasIdentity(true);
    };
    window.addEventListener('popstate', handlePopState);
    window.addEventListener('storage', handleStorage);
    return () => {
      window.removeEventListener('popstate', handlePopState);
      window.removeEventListener('storage', handleStorage);
    };
  }, []);

  const navigate = useCallback((nextPathname: string) => {
    if (nextPathname === window.location.pathname) return;
    if (nextPathname !== '/play') setActivePlaySession(null);
    window.history.pushState({}, '', nextPathname);
    setPathname(window.location.pathname);
  }, []);
  const gameAnalysisMatch = /^\/games\/([^/]+)\/analysis$/.exec(pathname);
  const gameAnalysisId = gameAnalysisMatch ? decodeRouteSegment(gameAnalysisMatch[1]) : null;
  let screen = <div data-testid="not-found">Page not found</div>;
  const isKnownPath = gameAnalysisId !== null || [
    '/', '/play', '/import', '/famous', '/analysis', '/games',
    '/explorer', '/training', '/puzzles', '/profile', '/coach', '/library', '/settings',
  ].includes(pathname);
  if (gameAnalysisId !== null) {
    screen = (
      <AnalysisScreen
        key={gameAnalysisId}
        gameId={gameAnalysisId}
        initialPly={Number(new URLSearchParams(window.location.search).get('ply'))}
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
          onNavigate={navigate}
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
    case '/puzzles':
      screen = <PuzzlesScreen key={puzzleLibraryRevision} />;
      break;
    case '/profile':
      screen = <ProfileScreen onNavigate={navigate} />;
      break;
    case '/coach':
      screen = <CoachDashboardScreen />;
      break;
    case '/settings':
      screen = <SettingsScreen />;
      break;
    case '/library':
      screen = (
        <section className="library-screen working-screen">
          <h1>Library</h1>
          <p>Explore an opening, study a master game, or bring your own game to review.</p>
          <Panel title="Choose a study path">
            <div className="library-links">
              <Button variant="primary" onClick={() => navigate('/explorer')}>Opening Explorer</Button>
              <Button onClick={() => navigate('/famous')}>Famous Games</Button>
              <Button onClick={() => navigate('/import')}>Import PGN</Button>
            </div>
          </Panel>
        </section>
      );
      break;
  }

  return (
    <PuzzleImportProvider onStartPuzzles={() => { setPuzzleLibraryRevision(value => value + 1); navigate('/puzzles'); }}>
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      <DesktopUpdateBanner />
      <header className="app-header">
        <div className="app-brand"><img src="/logo.svg" alt="" /><span className="app-brand-name">Scan64</span></div>
        <p>Your chess study table</p>
      </header>
      <nav className="app-nav" aria-label="Main navigation">
        {[
          ['Today', '/'], ['Play', '/play'], ['Review', '/games'],
          ['Train', '/training'], ['Puzzles', '/puzzles'], ['Profile', '/profile'],
          ['Library', '/library'], ['Coach', '/coach'], ['Settings', '/settings'],
        ].map(([label, path]) => {
          const selected = pathname === path
            || (path === '/games' && (pathname.startsWith('/games/') || pathname === '/analysis'))
            || (path === '/library' && ['/import', '/famous', '/explorer'].includes(pathname));
          return (
            <button
              key={path}
              aria-current={selected ? 'page' : undefined}
              aria-label={label}
              title={label}
              className={path === '/settings' ? 'settings-link' : undefined}
              disabled={!hasIdentity && isKnownPath && path !== '/' && path !== '/settings'}
              onClick={() => navigate(path)}
            ><NavigationIcon name={label} /><span>{label}</span></button>
          );
        })}
        <PuzzleImportFoot />
      </nav>
      <main id="main-content" ref={mainRef} tabIndex={-1} aria-label="Study content">
        {!hasIdentity && isKnownPath && pathname !== '/settings' ? (
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
              <Button variant="primary" type="submit" disabled={creatingPlayer || !displayName.trim()}>
                {creatingPlayer ? 'Creating player…' : 'Start learning'}
              </Button>
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
    </PuzzleImportProvider>
  );
}

export default App;
