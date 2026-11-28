/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
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

// Vitest stubs CSS imports, even with ?raw, so the fallback test reads the CSS file from disk.
// tests/e2e/material-boards.spec.ts checks the applied CSS in Chromium.
const manifest = JSON.parse(manifestText) as Manifest;
const materialCss = readFileSync(join(__dirname, 'components', 'boardMaterials.generated.css'), 'utf8');

interface CssBlock { prelude: string; body: string }

/** Splits CSS text into top-level `prelude { body }` blocks, with comments removed. */
function topLevelBlocks(css: string): CssBlock[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const blocks: CssBlock[] = [];
  let depth = 0;
  let start = 0;
  let open = 0;
  for (let index = 0; index < text.length; index += 1) {
    if (text[index] === '{') {
      if (depth === 0) open = index;
      depth += 1;
    } else if (text[index] === '}') {
      depth -= 1;
      expect(depth, 'balanced braces').toBeGreaterThanOrEqual(0);
      if (depth === 0) {
        blocks.push({ prelude: text.slice(start, open).trim(), body: text.slice(open + 1, index).trim() });
        start = index + 1;
      }
    }
  }
  expect(depth, 'balanced braces').toBe(0);
  return blocks;
}

function textureOf(body: string): string | undefined {
  return /--board-texture:\s*([^;]+);/.exec(body)?.[1].trim();
}

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

  it('sets a plain WebP texture first and an AVIF image-set() only inside @supports for type()', () => {
    // Safari 14-16 cannot parse type() in image-set(); there the texture must stay a plain url().
    const blocks = topLevelBlocks(materialCss);
    const supportsIndex = blocks.findIndex((block) => block.prelude.startsWith('@supports'));
    expect(supportsIndex, 'one @supports block').toBeGreaterThan(0);
    expect(blocks.filter((block) => block.prelude.startsWith('@')).length).toBe(1);
    const base = blocks.slice(0, supportsIndex);
    expect(blocks.slice(supportsIndex + 1), 'nothing after the @supports block').toEqual([]);
    expect(base.map((block) => block.prelude)).toEqual(
      manifest.boards.map((board) => `:root[data-board-theme='${board.id}']`),
    );
    for (const [index, block] of base.entries()) {
      const board = manifest.boards[index];
      expect(block.body, board.id).not.toContain('image-set');
      expect(textureOf(block.body), board.id).toBe(`url('${board.files.webp.url}')`);
    }

    const supports = blocks[supportsIndex];
    expect(supports.prelude.replace(/\s+/g, ' ')).toMatch(
      /^@supports \(background-image: image-set\(url\('[^']+'\) type\('image\/avif'\)\)\)$/,
    );
    const upgrades = topLevelBlocks(supports.body);
    expect(upgrades.map((block) => block.prelude)).toEqual(base.map((block) => block.prelude));
    for (const [index, block] of upgrades.entries()) {
      const { id, files } = manifest.boards[index];
      expect(textureOf(block.body), id).toBe(
        `image-set(url('${files.avif.url}') type('image/avif'), url('${files.webp.url}') type('image/webp'))`,
      );
    }
  });
});
