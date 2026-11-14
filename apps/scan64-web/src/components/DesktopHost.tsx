import { useEffect, useState } from 'react';
import App from '../App';
import { refreshDesktopConnection, restartDesktopServer } from '../platform';
import { Button, Panel } from './Primitives';

export function DesktopHost({ initialError }: { initialError: string | null }) {
  const [error, setError] = useState(initialError);
  const [restarting, setRestarting] = useState(false);

  useEffect(() => {
    if (error !== null) return;
    let stopped = false;
    let timer: number;
    const poll = async () => {
      try {
        await refreshDesktopConnection();
        if (!stopped) timer = window.setTimeout(() => { void poll(); }, 1_000);
      } catch (failure) {
        if (!stopped) setError(String(failure));
      }
    };
    timer = window.setTimeout(() => { void poll(); }, 1_000);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [error]);

  async function restart() {
    setRestarting(true);
    try {
      await restartDesktopServer();
      setError(null);
    } catch (failure) {
      setError(String(failure));
    } finally {
      setRestarting(false);
    }
  }

  if (error === null) return <App />;
  return (
    <main className="desktop-failure">
      <Panel>
        <h1>Scan64 could not connect to its server</h1>
        <p>Your saved games and player profile stay on this device. Restart the server to continue.</p>
        <div role="alert">
          <h2>Server details and log location</h2>
          <pre>{error}</pre>
        </div>
        <Button variant="primary" disabled={restarting} onClick={() => { void restart(); }}>
          {restarting ? 'Restarting…' : 'Restart'}
        </Button>
        {restarting && <p role="status">Starting the server. This can take up to 30 seconds.</p>}
      </Panel>
    </main>
  );
}
