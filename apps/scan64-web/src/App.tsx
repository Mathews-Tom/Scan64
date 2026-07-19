import { useState } from 'react';
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

function App() {
  const [currentView, setCurrentView] = useState<
    'home' | 'play' | 'import' | 'analysis' | 'explorer' | 'famous' | 'training' | 'profile' | 'coach'
  >('home');
  const [activePlaySession, setActivePlaySession] = useState<{session: PlaySessionRead, fen: string} | null>(null);
  const [activeAnalysisGameId, setActiveAnalysisGameId] = useState<string | undefined>();

  return (
    <>
      <nav>
        <button onClick={() => setCurrentView('home')}>Home</button>
        <button onClick={() => { setActivePlaySession(null); setCurrentView('play'); }}>Play Game</button>
        <button onClick={() => setCurrentView('import')}>Import PGN</button>
        <button onClick={() => setCurrentView('famous')}>Famous Games</button>
        <button onClick={() => setCurrentView('analysis')}>Analysis Board</button>
        <button onClick={() => setCurrentView('explorer')}>Opening Explorer</button>
        <button onClick={() => setCurrentView('training')}>Daily Training</button>
        <button onClick={() => setCurrentView('profile')}>Profile</button>
        <button onClick={() => setCurrentView('coach')}>Coach Dashboard</button>
      </nav>
      
      <main>
        {currentView === 'home' && <div>Welcome to Scan64</div>}
        {currentView === 'play' && (
          <PlayScreen 
            key={activePlaySession ? activePlaySession.session.id : 'new'} 
            initialSession={activePlaySession?.session} 
            initialFen={activePlaySession?.fen} 
          />
        )}
        {currentView === 'import' && (
          <PgnImportScreen
            onExploreAnalysis={(gameId) => {
              setActiveAnalysisGameId(gameId);
              setCurrentView('analysis');
            }}
          />
        )}
        {currentView === 'famous' && (
          <FamousGameStudyScreen
            onPlayFromHere={(session, fen) => {
              setActivePlaySession({ session, fen });
              setCurrentView('play');
            }}
          />
        )}
        {currentView === 'analysis' && (
          <AnalysisScreen
            gameId={activeAnalysisGameId}
            onPlayFromHere={(session, fen) => {
              setActivePlaySession({ session, fen });
              setCurrentView('play');
            }} 
          />
        )}
        {currentView === 'explorer' && (
          <OpeningExplorerScreen />
        )}
        {currentView === 'training' && (
          <DailyTrainingScreen />
        )}
        {currentView === 'profile' && (
          <ProfileScreen />
        )}
        {currentView === 'coach' && <CoachDashboardScreen />}
      </main>
    </>
  );
}

export default App;
