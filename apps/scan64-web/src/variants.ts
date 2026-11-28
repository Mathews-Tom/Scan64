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
