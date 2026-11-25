Working on detailed natural-material boards (wood grain, marble, granite) with a matte finish for the Scan64 web client.
Repo: D:/projects/Scan64 (you are in a worktree of it)
Branch: feat/363-material-boards (already checked out in your worktree; do not switch branches)

Issue: #363 — Add detailed natural-material boards (wood grain, marble, granite) with a matte finish
Read the issue body first: `gh issue view 363 -R Mathews-Tom/Scan64`.

Context: wave 1 merged FIDE board schemes and Staunton sets (#358, #359) into your base. Read boardAppearance.ts and docs/user-guide.md for the result.

Task:
- Add wood boards with visible fine grain: at least walnut/maple, rosewood/boxwood, mahogany/maple, ebony/boxwood. Add stone boards: at least one marble and one granite.
- Use CC0 textures only: Poly Haven (https://polyhaven.com/textures, CC0) or ambientCG (https://ambientcg.com, CC0). Use the albedo/diffuse maps only, so the look stays matte; add no gloss, specular, or glare effects.
- Process textures with a saved script (for example scripts/build-board-textures.py) and a TOML config listing source URL, licence, crop, tint, and output size. Output WebP (and AVIF if the build supports it) at sizes that stay sharp on 2x screens for a 1000 px board.
- Vary grain per square (offset, rotation by 180 degrees, or several crops) so no repeat is visible.
- Check light/dark square contrast for each board against the FIDE guidance documented by #358 in docs/user-guide.md; record the measured luminance ratio per board.
- State and test a budget: total texture bytes per board and time to first board paint on the desktop build. Document the numbers.
- Record every texture in THIRD_PARTY_NOTICES.md with source URL and licence.
- Do not change the default board or remove options. Add the new boards to Settings with a "Wood" and "Stone" grouping.
- Tests: Settings tests for new options and saved choices; a Playwright screenshot of each new board.

Target: 4+ wood and 2+ stone boards; all within the stated budget; web tests, lint, and Playwright pass.
