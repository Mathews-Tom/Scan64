import type { VariantName } from './api/types';

/** Display names of the variants the server reads (`scan64.chess.variants`). */
export const VARIANT_NAMES: Record<VariantName, string> = {
  chess: 'Standard',
  crazyhouse: 'Crazyhouse',
  '3check': 'Three-check',
  atomic: 'Atomic',
  antichess: 'Antichess',
  kingofthehill: 'King of the Hill',
  horde: 'Horde',
  racingkings: 'Racing Kings',
};

/** A stored game's variant as shown: a known variant's name, else the PGN tag as written. */
export function variantLabel(variant: string): string {
  return variant in VARIANT_NAMES ? VARIANT_NAMES[variant as VariantName] : variant;
}
