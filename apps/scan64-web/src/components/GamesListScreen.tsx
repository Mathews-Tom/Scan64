import { useEffect, useState } from 'react';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import type { PlayerGameRead } from '../api/types';

interface GamesListScreenProps {
  onOpenGame: (gameId: string) => void;
}

export function GamesListScreen({ onOpenGame }: GamesListScreenProps) {
  const [games, setGames] = useState<PlayerGameRead[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const loadGames = async (cursor?: string): Promise<void> => {
    const playerId = await ensurePlayerAuthorization(getOrCreatePlayerId());
    const page = cursor === undefined
      ? await ApiClient.getPlayerGames(playerId)
      : await ApiClient.getPlayerGames(playerId, cursor);
    setGames((currentGames) => cursor ? [...currentGames, ...page.items] : page.items);
    setNextCursor(page.next_cursor);
  };

  useEffect(() => {
    let cancelled = false;
    void loadGames()
      .catch((caught: unknown) => {
        if (!cancelled) setError(caught instanceof Error ? caught.message : 'Unable to load games');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
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
              {game.white} vs {game.black} — {game.result} — {game.date} — {game.diagnosis_count} diagnoses
            </button>
          </li>
        ))}
      </ul>
      {nextCursor && (
        <button
          type="button"
          onClick={() => {
            setLoading(true);
            void loadGames(nextCursor)
              .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : 'Unable to load games'))
              .finally(() => setLoading(false));
          }}
        >
          Load more games
        </button>
      )}
    </section>
  );
}
