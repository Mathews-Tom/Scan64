import { PuzzleImportProvider, PuzzleImportFoot } from './components/PuzzleImport';
import { UpdatesProvider, UpdatesFoot } from './components/Updates';
import { Component, lazy, Suspense, useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import './App.css';
import type { PlaySessionRead } from './api/types';
import { createActivePlayer, getStoredPlayerId } from './api/client';
import { NavigationIcon } from './components/NavigationIcon';
import { Button, Panel } from './components/Primitives';
import { isDesktop } from './platform';
import './components/WorkingScreens.css';
import { initializeAppearance } from './appearance';

const PlayScreen = lazy(() => import('./components/PlayScreen').then(module => ({ default: module.PlayScreen })));
const PgnImportScreen = lazy(() => import('./components/PgnImportScreen').then(module => ({ default: module.PgnImportScreen })));
const AnalysisScreen = lazy(() => import('./components/AnalysisScreen').then(module => ({ default: module.AnalysisScreen })));
const FamousGameStudyScreen = lazy(() => import('./components/FamousGameStudyScreen').then(module => ({ default: module.FamousGameStudyScreen })));
const OpeningExplorerScreen = lazy(() => import('./components/OpeningExplorerScreen').then(module => ({ default: module.OpeningExplorerScreen })));
const DailyTrainingScreen = lazy(() => import('./components/DailyTrainingScreen').then(module => ({ default: module.DailyTrainingScreen })));
const ProfileScreen = lazy(() => import('./components/ProfileScreen').then(module => ({ default: module.ProfileScreen })));
const CoachDashboardScreen = lazy(() => import('./components/CoachDashboardScreen').then(module => ({ default: module.CoachDashboardScreen })));
const GamesListScreen = lazy(() => import('./components/GamesListScreen').then(module => ({ default: module.GamesListScreen })));
const TodayScreen = lazy(() => import('./components/TodayScreen').then(module => ({ default: module.TodayScreen })));
const PuzzlesScreen = lazy(() => import('./components/PuzzlesScreen').then(module => ({ default: module.PuzzlesScreen })));
const SettingsScreen = lazy(() => import('./components/SettingsScreen').then(module => ({ default: module.SettingsScreen })));
const LessonPacksPanel = lazy(() => import('./components/LessonPacksPanel').then(module => ({ default: module.LessonPacksPanel })));

class RouteErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  override state = { error: null as Error | null };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error : new Error('Screen failed to load.') };
  }

  override render() {
    if (this.state.error) {
      return <Panel title="Screen unavailable">
        <p role="alert">This screen could not be loaded. {this.state.error.message}</p>
        <Button onClick={() => window.location.reload()}>Reload screen</Button>
      </Panel>;
    }
    return this.props.children;
  }
}

function RouteReady({ children, onReady }: { children: ReactNode; onReady: () => void }) {
  useEffect(onReady, [onReady]);
  return children;
}

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

type SidebarViewport = 'phone' | 'compact' | 'wide';
const sidebarPreferenceKey = 'scan64_sidebar_expanded';
function sidebarViewport(): SidebarViewport {
  return window.innerWidth < 768 && !isDesktop() ? 'phone' : window.innerWidth < 1200 ? 'compact' : 'wide';
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
  const [viewport, setViewport] = useState(sidebarViewport);
  const [wideExpanded, setWideExpanded] = useState(() => localStorage.getItem(sidebarPreferenceKey) !== 'false');
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sidebarRef = useRef<HTMLElement>(null);
  const sidebarToggleRef = useRef<HTMLButtonElement>(null);
  const drawerWasOpen = useRef(false);
  const drawerFocus = useRef<'toggle' | 'main'>('toggle');
  const focusSettingsPanel = useRef<string | null>(null);
  const drawerVisible = viewport === 'compact' && drawerOpen;
  const sidebarExpanded = viewport === 'wide' ? wideExpanded : drawerVisible;
  const dismissDrawer = useCallback((focus: 'toggle' | 'main' = 'toggle') => {
    drawerFocus.current = focus;
    setDrawerOpen(false);
  }, []);
  useEffect(() => {
    const resize = () => {
      const next = sidebarViewport();
      if (next !== viewport) {
        dismissDrawer(next === 'phone' ? 'main' : 'toggle');
        setViewport(next);
      }
    };
    window.addEventListener('resize', resize);
    return () => window.removeEventListener('resize', resize);
  }, [viewport, dismissDrawer]);
  useLayoutEffect(() => {
    if (!drawerVisible) {
      if (drawerWasOpen.current) {
        (drawerFocus.current === 'main' ? mainRef.current : sidebarToggleRef.current)?.focus();
      }
      drawerWasOpen.current = false;
      return;
    }
    drawerWasOpen.current = true;
    const sidebar = sidebarRef.current;
    sidebarToggleRef.current?.focus();
    const keepFocusInside = (event: FocusEvent) => {
      if (event.target instanceof Node && !sidebar?.contains(event.target)) sidebarToggleRef.current?.focus();
    };
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        dismissDrawer();
      } else if (event.key === 'Tab') {
        const controls = Array.from(sidebar?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], [tabindex="0"]') ?? [])
          .filter(control => {
            for (let element: HTMLElement | null = control; element && element !== sidebar; element = element.parentElement) {
              const style = getComputedStyle(element);
              if (element.hidden || style.display === 'none' || style.visibility !== 'visible') return false;
            }
            return true;
          });
        event.stopPropagation();
        const first = controls?.[0];
        const last = controls?.[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    const previousOverflow = document.documentElement.style.overflow;
    document.documentElement.style.overflow = 'hidden';
    document.addEventListener('focusin', keepFocusInside);
    document.addEventListener('keydown', handleKey, true);
    return () => {
      document.documentElement.style.overflow = previousOverflow;
      document.removeEventListener('focusin', keepFocusInside);
      document.removeEventListener('keydown', handleKey, true);
    };
  }, [drawerVisible, dismissDrawer]);
  const toggleSidebar = () => {
    if (viewport === 'compact') {
      if (drawerVisible) dismissDrawer();
      else setDrawerOpen(true);
    } else if (viewport === 'wide') {
      const expanded = !wideExpanded;
      setWideExpanded(expanded);
      localStorage.setItem(sidebarPreferenceKey, String(expanded));
    }
  };
  useLayoutEffect(() => {
    const titles: Record<string, string> = {
      '/': 'Today', '/play': 'Play', '/games': 'Your games', '/analysis': 'Analysis',
      '/training': 'Daily Training', '/puzzles': 'Puzzles', '/profile': 'Profile',
      '/coach': 'Coach', '/library': 'Library', '/import': 'Import games',
      '/famous': 'Famous Games', '/explorer': 'Opening Explorer', '/settings': 'Settings',
    };
    document.title = `${!hasIdentity && pathname !== '/settings' ? 'Welcome' : titles[pathname] ?? (pathname.startsWith('/games/') ? 'Game review' : 'Page not found')} · Scan64`;
    if (previousScreen.current.pathname !== pathname || previousScreen.current.hasIdentity !== hasIdentity) {
      mainRef.current?.focus();
    }
    previousScreen.current = { pathname, hasIdentity };
  }, [pathname, hasIdentity]);
  const focusRequestedSettingsPanel = useCallback(() => {
    if (focusSettingsPanel.current && pathname === '/settings' && !drawerVisible) {
      const panel = mainRef.current?.querySelector<HTMLElement>(`#${focusSettingsPanel.current}`);
      if (panel) {
        panel.focus();
        focusSettingsPanel.current = null;
      }
    }
  }, [pathname, drawerVisible]);
  useEffect(focusRequestedSettingsPanel, [focusRequestedSettingsPanel]);

  useEffect(() => {
    const handlePopState = () => {
      dismissDrawer('main');
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
  }, [dismissDrawer]);

  const navigate = useCallback((nextPathname: string) => {
    if (drawerWasOpen.current) dismissDrawer('main');
    if (nextPathname === window.location.pathname) return;
    if (nextPathname !== '/play') setActivePlaySession(null);
    window.history.pushState({}, '', nextPathname);
    setPathname(window.location.pathname);
  }, [dismissDrawer]);
  const openSettingsPanel = (id: 'puzzle-library' | 'app-updates') => {
    focusSettingsPanel.current = id;
    if (pathname === '/settings' && !drawerVisible) focusRequestedSettingsPanel();
    else navigate('/settings');
  };
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
          onOpenGame={(gameId) => navigate(`/games/${encodeURIComponent(gameId)}/analysis`)}
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
      screen = <GamesListScreen onOpenGame={(gameId) => navigate(`/games/${encodeURIComponent(gameId)}/analysis`)} onImportGames={() => navigate('/import')} />;
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
              <Button onClick={() => navigate('/import')}>Import games</Button>
            </div>
          </Panel>
          <Suspense fallback={<p role="status">Loading lesson packs…</p>}>
            <LessonPacksPanel />
          </Suspense>
        </section>
      );
      break;
  }

  return (
    <UpdatesProvider>
    <PuzzleImportProvider onStartPuzzles={() => { setPuzzleLibraryRevision(value => value + 1); navigate('/puzzles'); }}>
    <div className="app-shell" data-viewport={viewport} data-sidebar-expanded={sidebarExpanded} data-drawer-open={drawerVisible} data-native={isDesktop()}>
      <a className="skip-link" href="#main-content" inert={drawerVisible}>Skip to content</a>
      {drawerVisible ? <div className="sidebar-backdrop" aria-hidden="true" onClick={() => dismissDrawer()} /> : null}
      <div className="app-sidebar-slot">
      <aside className="app-sidebar" ref={sidebarRef} role={drawerVisible ? 'dialog' : undefined} aria-modal={drawerVisible ? true : undefined} aria-label={drawerVisible ? 'Navigation menu' : undefined}>
        <header className="app-header">
          <div className="app-brand"><img src="/logo.svg" alt="" /><span className="app-brand-name">Scan64</span></div>
          <p>Your chess study table</p>
          <button ref={sidebarToggleRef} className="sidebar-toggle" aria-expanded={sidebarExpanded} aria-controls="main-navigation" aria-label={sidebarExpanded ? 'Collapse sidebar' : 'Expand sidebar'} title={sidebarExpanded ? 'Collapse sidebar' : 'Expand sidebar'} onClick={toggleSidebar}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" aria-hidden="true"><path d={sidebarExpanded ? 'm14 6-6 6 6 6' : 'm10 6 6 6-6 6'} /></svg>
          </button>
        </header>
      <nav id="main-navigation" className="app-nav" aria-label="Main navigation">
        {[
          ['Today', '/'], ['Play', '/play'], ['Review', '/games'],
          ['Train', '/training'], ['Puzzles', '/puzzles'], ['Profile', '/profile'],
          ['Library', '/library'], ['Coach', '/coach'], ['Settings', '/settings'],
        ].map(([label, path]) => {
          const selected = pathname === path
            || (path === '/games' && (pathname.startsWith('/games/') || ['/analysis', '/import'].includes(pathname)))
            || (path === '/library' && ['/famous', '/explorer'].includes(pathname));
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
        <PuzzleImportFoot onOpenSettings={() => openSettingsPanel('puzzle-library')} />
        <UpdatesFoot onOpenSettings={() => openSettingsPanel('app-updates')} />
      </nav>
      </aside>
      </div>
      <main id="main-content" ref={mainRef} tabIndex={-1} aria-label="Study content" inert={drawerVisible}>
        <div className="app-canvas">
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
            {['/games', '/analysis', '/import'].includes(pathname) || gameAnalysisId !== null ? (
              <nav className="section-nav" aria-label="Review navigation">
                <button onClick={() => navigate('/games')}>Your Games</button>
                <button onClick={() => navigate('/analysis')}>Analysis Board</button>
                <button onClick={() => navigate('/import')}>Import games</button>
              </nav>
            ) : null}
            <RouteErrorBoundary key={pathname}>
              <Suspense fallback={<p role="status">Loading screen…</p>}>
                <RouteReady onReady={focusRequestedSettingsPanel}>{screen}</RouteReady>
              </Suspense>
            </RouteErrorBoundary>
          </>
        )}
        </div>
      </main>
    </div>
    </PuzzleImportProvider>
    </UpdatesProvider>
  );
}

export default App;
