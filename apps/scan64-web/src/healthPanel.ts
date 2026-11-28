import type { HealthLevel, HealthMeterRead, HealthTrend } from './api/types';

// A switch kept in localStorage, with a session value when storage is refused.
function preference(key: string, fallback: boolean): { read: () => boolean; save: (enabled: boolean) => boolean } {
  let session: boolean | undefined;
  return {
    read: () => {
      if (session !== undefined) return session;
      try {
        const stored = localStorage.getItem(key);
        if (stored === 'true' || stored === 'false') return stored === 'true';
      } catch { /* Restricted storage still permits a session preference. */ }
      return fallback;
    },
    save: (enabled: boolean) => {
      session = enabled;
      try {
        localStorage.setItem(key, String(enabled));
        return true;
      } catch { return false; }
    },
  };
}

// The position health panel is on by default (#416); the Settings switch turns it off.
const panel = preference('scan64-health-panel', true);
export const readHealthPanel = panel.read;
export const saveHealthPanel = panel.save;

// The meters during play (#417): on by default, switched beside the hint options.
const play = preference('scan64-play-health', true);
export const readPlayHealth = play.read;
export const savePlayHealth = play.save;

// Reading the meters during play counts as the first hint level only when this is on.
const counts = preference('scan64-play-health-counts', false);
export const readPlayHealthCounts = counts.read;
export const savePlayHealthCounts = counts.save;

// Segments per meter: the fill rounds to whole segments.
export const SEGMENTS = 12;

export const LEVEL_WORDS: Record<HealthLevel, string> = { good: 'good', fair: 'fair', low: 'low' };
export const TREND_MARKS: Record<HealthTrend, string> = { rising: '▲', falling: '▼', steady: '' };

export function meterText(name: string, meter: HealthMeterRead): string {
  return meter.trend === 'steady' ? `${name}: ${LEVEL_WORDS[meter.level]}` : `${name}: ${LEVEL_WORDS[meter.level]}, ${meter.trend}`;
}

export function filledSegments(health: number): number {
  return Math.max(0, Math.min(SEGMENTS, Math.round(health * SEGMENTS)));
}
