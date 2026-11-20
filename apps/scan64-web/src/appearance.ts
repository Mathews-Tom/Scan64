export type Appearance = 'system' | 'light' | 'dark';

export const APPEARANCE_STORAGE_KEY = 'scan64-appearance';

export function readAppearance(): Appearance {
  try {
    const stored = window.localStorage.getItem(APPEARANCE_STORAGE_KEY);
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // Storage can be unavailable in private or restricted browser contexts.
  }
  return 'system';
}

export function applyAppearance(appearance: Appearance) {
  if (typeof document !== 'undefined') document.documentElement.dataset.appearance = appearance;
}

/** System appearance and native control colour schemes follow the token CSS media query. */
export function initializeAppearance(): Appearance {
  const appearance = readAppearance();
  applyAppearance(appearance);
  return appearance;
}
