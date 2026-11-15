export const PIECE_SET_OPTIONS = [
  { value: 'chessnut', label: 'Chessnut' },
  { value: 'spatial', label: 'Spatial' },
  { value: 'firi', label: 'Firi' },
  { value: 'california', label: 'California (non-commercial)' },
  { value: 'maestro', label: 'Maestro (non-commercial)' },
  { value: 'staunty', label: 'Staunty (non-commercial)' },
] as const;

export const BOARD_THEME_OPTIONS = [
  { value: 'slate', label: 'Slate' },
  { value: 'sage', label: 'Sage' },
  { value: 'graphite', label: 'Graphite' },
  { value: 'walnut', label: 'Walnut' },
  { value: 'sand', label: 'Sand' },
] as const;

export type PieceSet = typeof PIECE_SET_OPTIONS[number]['value'];
export type BoardTheme = typeof BOARD_THEME_OPTIONS[number]['value'];
export interface BoardAppearance { pieceSet: PieceSet; boardTheme: BoardTheme }

function readChoice<T extends string>(key: string, options: readonly { value: T }[], fallback: T): T {
  try {
    const stored = window.localStorage.getItem(key);
    return options.find((option) => option.value === stored)?.value ?? fallback;
  } catch {
    return fallback;
  }
}

export function readBoardAppearance(): BoardAppearance {
  return {
    pieceSet: readChoice('scan64-piece-set', PIECE_SET_OPTIONS, 'chessnut'),
    boardTheme: readChoice('scan64-board-theme', BOARD_THEME_OPTIONS, 'slate'),
  };
}

export function applyBoardAppearance({ pieceSet, boardTheme }: BoardAppearance): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.pieceSet = pieceSet;
  document.documentElement.dataset.boardTheme = boardTheme;
}

/** Apply before render so every shared board starts with the saved graphics. */
export function initializeBoardAppearance(): BoardAppearance {
  const appearance = readBoardAppearance();
  applyBoardAppearance(appearance);
  return appearance;
}

/** A failed save does not prevent the current session's selection from applying. */
export function saveBoardChoice(key: 'pieceSet', value: PieceSet): boolean;
export function saveBoardChoice(key: 'boardTheme', value: BoardTheme): boolean;
export function saveBoardChoice(key: 'pieceSet' | 'boardTheme', value: PieceSet | BoardTheme): boolean {
  try {
    window.localStorage.setItem(key === 'pieceSet' ? 'scan64-piece-set' : 'scan64-board-theme', value);
    return true;
  } catch {
    return false;
  }
}
