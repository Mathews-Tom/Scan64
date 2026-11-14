import { useEffect, useState } from 'react';
import { ApiClient, ensurePlayerAuthorization, getOrCreatePlayerId } from '../api/client';
import type { PlayerGameRead } from '../api/types';
import { latestGameLabel } from './gameLabel';
import { Button, EmptyState, ListRow } from './Primitives';

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
    <section className="working-screen" data-testid="games-list-screen">
      <h1>Your games</h1>
      {loading && <p role="status">Loading games...</p>}
      {error && <div className="error" role="alert">{error}</div>}
      {!loading && !error && games.length === 0 && <EmptyState title="No games yet"><p>Play a game or import a PGN to start your review record.</p></EmptyState>}
      <ul className="working-list">
        {games.map((game) => (
          <ListRow key={game.id}
            label={<Button onClick={() => onOpenGame(game.id)}>
              {latestGameLabel(game, getOrCreatePlayerId())} — {game.result} — {game.diagnosis_count} diagnoses
            </Button>}
          />
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
