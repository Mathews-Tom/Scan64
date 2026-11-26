import { describe, expect, it } from 'vitest';
import manifestText from './boardMaterials.generated.json?raw';
import { BOARD_THEME_OPTIONS } from './boardAppearance';

interface MaterialBoard {
  id: string;
  label: string;
  group: 'wood' | 'stone';
  files: Record<'webp' | 'avif', { url: string; bytes: number }>;
  light_mean: string;
  dark_mean: string;
  luminance_ratio: number;
  max_texture_bytes: number;
}
interface Manifest { board_px: number; budget_bytes_per_board: number; min_luminance_ratio: number; boards: MaterialBoard[] }

// Vitest stubs CSS imports; tests/e2e/material-boards.spec.ts checks the generated CSS in Chromium.
const manifest = JSON.parse(manifestText) as Manifest;

function relativeLuminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((start) => {
    const channel = Number.parseInt(hex.slice(start, start + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

describe('Material boards', () => {
  it('offers at least four wood and two stone boards that match the texture build', () => {
    const materials = BOARD_THEME_OPTIONS.filter((option) => option.material !== 'flat')
      .map(({ value, label, material }) => ({ id: value, label, group: material }));
    expect(materials).toEqual(manifest.boards.map(({ id, label, group }) => ({ id, label, group })));
    expect(materials.filter((board) => board.group === 'wood').length).toBeGreaterThanOrEqual(4);
    expect(materials.filter((board) => board.group === 'stone').length).toBeGreaterThanOrEqual(2);
  });

  it('keeps every board within the stated texture budget at 2x for a 1000 px board', () => {
    expect(manifest.board_px).toBeGreaterThanOrEqual(2000);
    expect(manifest.budget_bytes_per_board).toBe(700_000);
    for (const board of manifest.boards) {
      expect(board.max_texture_bytes).toBe(Math.max(board.files.webp.bytes, board.files.avif.bytes));
      expect(board.max_texture_bytes, board.id).toBeLessThanOrEqual(manifest.budget_bytes_per_board);
    }
  });

  it('keeps at least the light/dark luminance ratio of the default Brown and cream palette', () => {
    const brown = (relativeLuminance('#f0d9b5') + 0.05) / (relativeLuminance('#9e7150') + 0.05);
    expect(manifest.min_luminance_ratio).toBeGreaterThanOrEqual(Math.floor(brown * 100) / 100);
    for (const board of manifest.boards) {
      expect(board.luminance_ratio, board.id).toBeGreaterThanOrEqual(manifest.min_luminance_ratio);
      const meanRatio = (relativeLuminance(board.light_mean) + 0.05) / (relativeLuminance(board.dark_mean) + 0.05);
      expect(meanRatio, board.id).toBeGreaterThanOrEqual(manifest.min_luminance_ratio);
    }
  });
});
