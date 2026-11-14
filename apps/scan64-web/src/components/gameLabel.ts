import type { PlayerGameRead } from '../api/types';

export function latestGameLabel(game: PlayerGameRead, playerId: string): string {
  const name = (value: string, side: string) => {
    if (value === playerId) return `You (${side})`;
    if (/^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(value)) return `${side} player`;
    return value || `${side} player`;
  };
  const stored = game.date.replace(/\./g, '-');
  const date = new Date(/^\d{4}-\d{2}-\d{2}$/.test(stored) ? `${stored}T12:00:00Z` : game.created_at);
  const day = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString('en-GB', {
    day: 'numeric', month: 'short', timeZone: 'UTC',
  });
  return `${name(game.white, 'White')} vs ${name(game.black, 'Black')}${day ? ` · ${day}` : ''}`;
}
