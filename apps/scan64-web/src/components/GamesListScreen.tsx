import { useEffect, useState } from 'react';
import { ApiClient, getOrCreatePlayerId } from '../api/client';
import type { PlayerGameRead } from '../api/types';

interface GamesListScreenProps {
  onOpenGame: (gameId: string) => void;
}

export function GamesListScreen({ onOpenGame }: GamesListScreenProps) {
  const [games, setGames] = useState<PlayerGameRead[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const page = await ApiClient.getPlayerGames(getOrCreatePlayerId());
        if (!cancelled) setGames(page.items);
      } catch (caught: unknown) {
        if (!cancelled) setError(caught instanceof Error ? caught.message : 'Unable to load games');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  return (
    <section data-testid="games-list-screen">
      <h1>Your games</h1>
      {loading && <p>Loading games...</p>}
      {error && <div className="error">{error}</div>}
      {!loading && !error && games.length === 0 && <p>No games yet.</p>}
      <ul>
        {games.map((game) => (
          <li key={game.id}>
            <button onClick={() => onOpenGame(game.id)}>
              {game.white} vs {game.black} — {game.result} — {new Date(game.created_at).toLocaleDateString()} — {game.diagnosis_count} diagnoses
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
