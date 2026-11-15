// Latest release page is deliberately the fallback: it cannot become a stale installer.
// Explicit versioned assets below are the verified 0.7.0 compatibility baseline.
const release = 'https://github.com/Mathews-Tom/Scan64/releases/download/v0.7.0/';
const platforms = {
  mac: { name: 'macOS · Apple silicon', asset: 'Scan64_0.7.0_aarch64.dmg' },
  windows: { name: 'Windows · x64', asset: 'Scan64_0.7.0_x64-setup.exe' },
  linux: { name: 'Linux · x64 AppImage', asset: 'Scan64_0.7.0_amd64.AppImage' },
};
const platform = `${navigator.userAgentData?.platform || ''} ${navigator.platform || ''} ${navigator.userAgent || ''}`;
// iOS/iPadOS must not receive the macOS desktop installer.
const mobile = /iPhone|iPad|iPod|Android/i.test(platform) || (/Mac/i.test(platform) && navigator.maxTouchPoints > 1);
const os = mobile ? null : /Win/i.test(platform) ? 'windows' : /Mac/i.test(platform) ? 'mac' : /Linux|X11/i.test(platform) ? 'linux' : null;
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
deb.href = release + 'Scan64_0.7.0_amd64.deb';
deb.textContent = 'Linux · x64 Debian package';
alternatives.append(deb);
const pieces = ['♜♞♝♛♚♝♞♜','♟♟♟♟♟♟♟♟','        ','        ','        ','        ','♙♙♙♙♙♙♙♙','♖♘♗♕♔♗♘♖'];
for (const board of document.querySelectorAll('[data-board]')) {
  for (let row = 0; row < 8; row++) for (let col = 0; col < 8; col++) {
    const square = document.createElement('span');
    square.className = `square${(row + col) % 2 ? ' dark' : ''}${row > 5 ? ' white-piece' : ''}`;
    square.textContent = pieces[row][col];
    square.setAttribute('aria-hidden', 'true');
    board.append(square);
  }
}
