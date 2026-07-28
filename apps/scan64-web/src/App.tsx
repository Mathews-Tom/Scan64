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


function App() {
  const [pathname, setPathname] = useState(() => window.location.pathname);
  const [activePlaySession, setActivePlaySession] = useState<{session: PlaySessionRead, fen: string} | null>(null);
  const [activeAnalysisGameId, setActiveAnalysisGameId] = useState<string | undefined>();

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
  let screen = <div data-testid="not-found">Page not found</div>;
  if (gameAnalysisMatch) {
    screen = (
      <AnalysisScreen
        gameId={decodeURIComponent(gameAnalysisMatch[1])}
        onPlayFromHere={(session, fen) => {
          setActivePlaySession({ session, fen });
          navigate('/play');
        }}
      />
    );
  } else switch (pathname) {
    case '/':
      screen = <div>Welcome to Scan64</div>;
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
  }

  return (
    <>
      <nav>
        <button onClick={() => navigate('/')}>Home</button>
        <button onClick={() => { setActivePlaySession(null); navigate('/play'); }}>Play Game</button>
        <button onClick={() => navigate('/import')}>Import PGN</button>
        <button onClick={() => navigate('/famous')}>Famous Games</button>
        <button onClick={() => navigate('/games')}>Your Games</button>
        <button onClick={() => navigate('/analysis')}>Analysis Board</button>
        <button onClick={() => navigate('/explorer')}>Opening Explorer</button>
        <button onClick={() => navigate('/training')}>Daily Training</button>
        <button onClick={() => navigate('/profile')}>Profile</button>
        <button onClick={() => navigate('/coach')}>Coach Dashboard</button>
      </nav>

      <main>{screen}</main>
    </>
  );
}

export default App;
