import type { PlaySessionRead } from './types';

export const DEFAULT_BOT_LEVEL = '1';

/** Older servers did not persist a human color; retain their unrestricted-turn behavior. */
export function sessionPlayerColor(session: PlaySessionRead | null): 'white' | 'black' | undefined {
  const color = session?.player_color ?? session?.opponent_config?.player_color;
  return color === 'white' || color === 'black' ? color : undefined;
}
