import { useState } from 'react';
import './App.css';
import { PlayScreen } from './components/PlayScreen';
import { PgnImportScreen } from './components/PgnImportScreen';
import { AnalysisScreen } from './components/AnalysisScreen';

function App() {
  const [currentView, setCurrentView] = useState<'home' | 'play' | 'import' | 'analysis'>('home');

  return (
    <>
      <nav>
        <button onClick={() => setCurrentView('home')}>Home</button>
        <button onClick={() => setCurrentView('play')}>Play Game</button>
        <button onClick={() => setCurrentView('import')}>Import PGN</button>
        <button onClick={() => setCurrentView('analysis')}>Analysis Board</button>
      </nav>
      
      <main>
        {currentView === 'home' && <div>Welcome to Scan64</div>}
        {currentView === 'play' && <PlayScreen />}
        {currentView === 'import' && <PgnImportScreen />}
        {currentView === 'analysis' && <AnalysisScreen />}
      </main>
    </>
  );
}

export default App;
