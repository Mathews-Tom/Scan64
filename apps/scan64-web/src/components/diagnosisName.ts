export function diagnosisName(code: string): string {
  if (code === 'threat_processing.allowed_mate') return 'Allowed checkmate';
  return code.replaceAll('_', ' ').replaceAll('.', ' / ');
}
