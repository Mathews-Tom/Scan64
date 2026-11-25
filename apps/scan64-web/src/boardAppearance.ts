export const PIECE_SET_OPTIONS = [
  { value: 'cburnett', label: 'Staunton (cburnett)' },
  { value: 'merida', label: 'Merida' },
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

function readChoice<T extends string>(key: string, options: readonly { value: T }[], fallback: T, sessionValue?: string): T {
  const sessionChoice = options.find((option) => option.value === sessionValue);
  if (sessionChoice) return sessionChoice.value;
  try {
    const stored = window.localStorage.getItem(key);
    return options.find((option) => option.value === stored)?.value ?? fallback;
  } catch {
    return fallback;
  }
}

function readBoardChoices(session?: DOMStringMap): BoardAppearance {
  return {
    pieceSet: readChoice('scan64-piece-set', PIECE_SET_OPTIONS, 'cburnett', session?.pieceSet),
    boardTheme: readChoice('scan64-board-theme', BOARD_THEME_OPTIONS, 'slate', session?.boardTheme),
  };
}

/** The applied document choices remain authoritative until the page reloads. */
export function readBoardAppearance(): BoardAppearance {
  return readBoardChoices(typeof document === 'undefined' ? undefined : document.documentElement.dataset);
}

export function applyBoardAppearance({ pieceSet, boardTheme }: BoardAppearance): void {
  if (typeof document === 'undefined') return;
  document.documentElement.dataset.pieceSet = pieceSet;
  document.documentElement.dataset.boardTheme = boardTheme;
}

/** Apply before render so every shared board starts with the saved graphics. */
export function initializeBoardAppearance(): BoardAppearance {
  const appearance = readBoardChoices();
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
