// Lucide interface paths, ISC licence: https://lucide.dev/license
const paths: Record<string, string[]> = {
  Today: ['m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1Z'],
  Play: ['m8 5 11 7-11 7Z'],
  Review: ['M3 3v18h18', 'm7 14 4-4 4 3 6-7'],
  Train: ['M12 8v4l3 3', 'M12 3a9 9 0 1 0 9 9', 'M16 3h5v5'],
  Puzzles: ['M19 13h-2a2 2 0 1 1 0-4h2V5h-4V3a2 2 0 1 0-4 0v2H5v6H3a2 2 0 1 0 0 4h2v6h6v-2a2 2 0 1 1 4 0v2h4Z'],
  Profile: ['M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2', 'M16 5a4 4 0 1 1-8 0 4 4 0 0 1 8 0'],
  Library: ['M12 7v14', 'M3 3h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5v16h-5a4 4 0 0 0-4 2 4 4 0 0 0-4-2H3Z'],
  Coach: ['M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8Z'],
  Settings: ['M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8', 'm9 3 1-2h4l1 2 2 1 2 0 2 4-1 2v4l1 2-2 4h-2l-2 1-1 2h-4l-1-2-2-1H5l-2-4 1-2v-4L3 8l2-4h2Z'],
};
export function NavigationIcon({ name }: { name: string }) {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{paths[name]?.map((path) => <path key={path} d={path} />)}</svg>;
}
