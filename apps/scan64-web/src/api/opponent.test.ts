import { describe, expect, it } from 'vitest';
import { DEFAULT_BOT_LEVEL, sessionPlayerColor } from './opponent';
import type { PlaySessionRead } from './types';

const session: PlaySessionRead = {
  id: 'session', player_id: 'player', opponent_config: {}, status: 'active', coach_mode: false,
};

describe('bot session configuration', () => {
  it('defaults all creators to level one', () => {
    expect(DEFAULT_BOT_LEVEL).toBe('1');
  });
  it('prefers explicit resolved color over stored config', () => {
    expect(sessionPlayerColor({ ...session, player_color: 'black', opponent_config: { player_color: 'white' } })).toBe('black');
  });
  it('uses persisted color after reload', () => {
    expect(sessionPlayerColor({ ...session, opponent_config: { player_color: 'black' } })).toBe('black');
  });
  it('retains legacy behavior without inferring color from the current turn', () => {
    expect(sessionPlayerColor(session)).toBeUndefined();
    expect(sessionPlayerColor(null)).toBeUndefined();
    expect(sessionPlayerColor({ ...session, opponent_config: { player_color: 'random' } })).toBeUndefined();
  });
});
