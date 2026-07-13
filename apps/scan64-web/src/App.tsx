import { useState } from 'react';
import './App.css';
import { PlayScreen } from './components/PlayScreen';

function App() {
  const [currentView, setCurrentView] = useState<'home' | 'play'>('home');

  return (
    <>
      <nav>
        <button onClick={() => setCurrentView('home')}>Home</button>
        <button onClick={() => setCurrentView('play')}>Play Game</button>
      </nav>
      
      <main>
        {currentView === 'home' && <div>Welcome to Scan64</div>}
        {currentView === 'play' && <PlayScreen />}
      </main>
    </>
  );
}

export default App;
