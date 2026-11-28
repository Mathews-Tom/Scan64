import type { HealthLevel, HealthMeterRead, HealthTrend } from './api/types';

// The position health panel is on by default (#416); the Settings switch turns it off.
const STORAGE_KEY = 'scan64-health-panel';
let sessionPreference: boolean | undefined;

export function readHealthPanel(): boolean {
  if (sessionPreference !== undefined) return sessionPreference;
  try {
    if (localStorage.getItem(STORAGE_KEY) === 'false') return false;
  } catch { /* Restricted storage still permits a session preference. */ }
  return true;
}

export function saveHealthPanel(enabled: boolean): boolean {
  sessionPreference = enabled;
  try {
    localStorage.setItem(STORAGE_KEY, String(enabled));
    return true;
  } catch { return false; }
}

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
