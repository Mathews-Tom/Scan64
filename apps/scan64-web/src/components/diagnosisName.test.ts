import { describe, expect, it } from 'vitest';
import { diagnosisName } from './diagnosisName';

describe('diagnosisName', () => {
  it('names the allowed-mate diagnosis without calling it a material loss', () => {
    expect(diagnosisName('threat_processing.allowed_mate')).toBe('Allowed checkmate');
  });

  it('keeps the existing presentation for other diagnosis codes', () => {
    expect(diagnosisName('board_awareness.hanging_piece')).toBe('board awareness / hanging piece');
  });
});
