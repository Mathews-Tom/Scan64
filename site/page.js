// Latest release page is deliberately the fallback: it cannot become a stale installer.
// Explicit versioned assets below are the published, verified 1.0.0 installers.
const version = '1.0.0';
const release = `https://github.com/Mathews-Tom/Scan64/releases/download/v${version}/`;
const platforms = {
  mac: { name: 'macOS · Apple silicon', asset: `Scan64_${version}_aarch64.dmg` },
  windows: { name: 'Windows · x64', asset: `Scan64_${version}_x64-setup.exe` },
  linux: { name: 'Linux · x64 AppImage', asset: `Scan64_${version}_amd64.AppImage` },
};
const platform = `${navigator.userAgentData?.platform || ''} ${navigator.platform || ''} ${navigator.userAgent || ''}`;
// Mobile devices, ChromeOS and Linux ARM have no supported desktop installer.
const unsupported = /iPhone|iPad|iPod|Android|CrOS/i.test(platform) || (/Mac/i.test(platform) && navigator.maxTouchPoints > 1) || (/Linux/i.test(platform) && /\b(?:aarch64|arm\w*)\b/i.test(platform));
const os = unsupported ? null : /Win/i.test(platform) ? 'windows' : /Mac/i.test(platform) ? 'mac' : /Linux/i.test(platform) ? 'linux' : null;
if (os) {
  document.querySelectorAll('[data-download]').forEach(link => { link.href = release + platforms[os].asset; });
  document.querySelector('[data-platform]').textContent = platforms[os].name;
}
const alternatives = document.querySelector('[data-other]');
alternatives.replaceChildren();
for (const [key, value] of Object.entries(platforms)) {
  if (key === os) continue;
  const link = document.createElement('a');
  link.href = release + value.asset;
  link.textContent = value.name;
  alternatives.append(link);
}
const deb = document.createElement('a');
deb.href = release + `Scan64_${version}_amd64.deb`;
deb.textContent = 'Linux · x64 Debian package';
alternatives.append(deb);
const pieces = ['rnbqkbnr', 'pppppppp', '', '', '', '', 'PPPPPPPP', 'RNBQKBNR'];
for (const board of document.querySelectorAll('[data-board]')) {
  for (let row = 0; row < 8; row++) for (let col = 0; col < 8; col++) {
    const square = document.createElement('span');
    square.className = `square${(row + col) % 2 ? ' dark' : ''}`;
    const piece = pieces[row][col];
    if (piece) square.style.backgroundImage = `url(pieces/cburnett/${piece === piece.toUpperCase() ? 'w' : 'b'}${piece.toUpperCase()}.svg)`;
    square.setAttribute('aria-hidden', 'true');
    board.append(square);
  }
}
