import { useState } from 'react';
import './App.css';
import { PlayScreen } from './components/PlayScreen';
import { PgnImportScreen } from './components/PgnImportScreen';
import { AnalysisScreen } from './components/AnalysisScreen';
import type { PlaySessionRead } from './api/types';

function App() {
  const [currentView, setCurrentView] = useState<'home' | 'play' | 'import' | 'analysis'>('home');
  const [activePlaySession, setActivePlaySession] = useState<{session: PlaySessionRead, fen: string} | null>(null);
  const [activeAnalysisGameId, setActiveAnalysisGameId] = useState<string | undefined>();

  return (
    <>
      <nav>
        <button onClick={() => setCurrentView('home')}>Home</button>
        <button onClick={() => { setActivePlaySession(null); setCurrentView('play'); }}>Play Game</button>
        <button onClick={() => setCurrentView('import')}>Import PGN</button>
        <button onClick={() => setCurrentView('analysis')}>Analysis Board</button>
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
        {currentView === 'analysis' && (
          <AnalysisScreen
            gameId={activeAnalysisGameId}
            onPlayFromHere={(session, fen) => {
              setActivePlaySession({ session, fen });
              setCurrentView('play');
            }} 
          />
        )}
      </main>
    </>
  );
}

export default App;
