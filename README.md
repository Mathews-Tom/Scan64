# M81 identity — live browser evidence

Source stack: root `ba17acd4959d91cfbaef406ab5653c291c0769ff`, board `dac6822bafbbf881ba67948d09b49f258b8ef5fd`, leaf `bb1ce03f5ff00c5dd18c7d675001b89e866a094d`. These images show the combined stack, not three different source revisions.

Six screens × four widths × two appearance grades. The existing `apps/scan64-web/scripts/m81-live-evidence.mjs` uses fresh headless Chromium contexts against a real local API and a disposable SQLite database, without route interception. Interaction, reflow, bundled-font and request-host results are recorded in [observations.json](observations.json).

The reproducible puzzle input is an imported legal four-ply opening-line fixture (`e2e4 e7e5 g1f3 b8c6` from the starting position; rating 1000, rating deviation 100, popularity 80, 100 plays). It is a deterministic move-entry fixture, not a real Lichess puzzle-quality or chess-improvement measurement. The player solves `e7e5`, then `b8c6`. Bot replies and game analysis use the real local Stockfish/API; explanation configuration is unset so the supported local/template path is used.

The script creates a player, plays e4 and receives a bot reply, opens a hint, resigns and analyses/reviews the game, solves the fixture, opens Profile, and changes/reloads Appearance. Human Safari/VoiceOver acceptance remains a separate pending M70 observation; these automated checks do not establish assistive-technology conformance.

| Viewport / appearance | Today | Play + hint | Game review | Puzzles | Profile | Settings |
|---|---|---|---|---|---|---|
| 1440 / light | [![Today, 1440px, light](today-1440-light.png)](today-1440-light.png) | [![Play + hint, 1440px, light](play-1440-light.png)](play-1440-light.png) | [![Game review, 1440px, light](review-1440-light.png)](review-1440-light.png) | [![Puzzles, 1440px, light](puzzles-1440-light.png)](puzzles-1440-light.png) | [![Profile, 1440px, light](profile-1440-light.png)](profile-1440-light.png) | [![Settings, 1440px, light](settings-1440-light.png)](settings-1440-light.png) |
| 1440 / dark | [![Today, 1440px, dark](today-1440-dark.png)](today-1440-dark.png) | [![Play + hint, 1440px, dark](play-1440-dark.png)](play-1440-dark.png) | [![Game review, 1440px, dark](review-1440-dark.png)](review-1440-dark.png) | [![Puzzles, 1440px, dark](puzzles-1440-dark.png)](puzzles-1440-dark.png) | [![Profile, 1440px, dark](profile-1440-dark.png)](profile-1440-dark.png) | [![Settings, 1440px, dark](settings-1440-dark.png)](settings-1440-dark.png) |
| 1024 / light | [![Today, 1024px, light](today-1024-light.png)](today-1024-light.png) | [![Play + hint, 1024px, light](play-1024-light.png)](play-1024-light.png) | [![Game review, 1024px, light](review-1024-light.png)](review-1024-light.png) | [![Puzzles, 1024px, light](puzzles-1024-light.png)](puzzles-1024-light.png) | [![Profile, 1024px, light](profile-1024-light.png)](profile-1024-light.png) | [![Settings, 1024px, light](settings-1024-light.png)](settings-1024-light.png) |
| 1024 / dark | [![Today, 1024px, dark](today-1024-dark.png)](today-1024-dark.png) | [![Play + hint, 1024px, dark](play-1024-dark.png)](play-1024-dark.png) | [![Game review, 1024px, dark](review-1024-dark.png)](review-1024-dark.png) | [![Puzzles, 1024px, dark](puzzles-1024-dark.png)](puzzles-1024-dark.png) | [![Profile, 1024px, dark](profile-1024-dark.png)](profile-1024-dark.png) | [![Settings, 1024px, dark](settings-1024-dark.png)](settings-1024-dark.png) |
| 768 / light | [![Today, 768px, light](today-768-light.png)](today-768-light.png) | [![Play + hint, 768px, light](play-768-light.png)](play-768-light.png) | [![Game review, 768px, light](review-768-light.png)](review-768-light.png) | [![Puzzles, 768px, light](puzzles-768-light.png)](puzzles-768-light.png) | [![Profile, 768px, light](profile-768-light.png)](profile-768-light.png) | [![Settings, 768px, light](settings-768-light.png)](settings-768-light.png) |
| 768 / dark | [![Today, 768px, dark](today-768-dark.png)](today-768-dark.png) | [![Play + hint, 768px, dark](play-768-dark.png)](play-768-dark.png) | [![Game review, 768px, dark](review-768-dark.png)](review-768-dark.png) | [![Puzzles, 768px, dark](puzzles-768-dark.png)](puzzles-768-dark.png) | [![Profile, 768px, dark](profile-768-dark.png)](profile-768-dark.png) | [![Settings, 768px, dark](settings-768-dark.png)](settings-768-dark.png) |
| 390 / light | [![Today, 390px, light](today-390-light.png)](today-390-light.png) | [![Play + hint, 390px, light](play-390-light.png)](play-390-light.png) | [![Game review, 390px, light](review-390-light.png)](review-390-light.png) | [![Puzzles, 390px, light](puzzles-390-light.png)](puzzles-390-light.png) | [![Profile, 390px, light](profile-390-light.png)](profile-390-light.png) | [![Settings, 390px, light](settings-390-light.png)](settings-390-light.png) |
| 390 / dark | [![Today, 390px, dark](today-390-dark.png)](today-390-dark.png) | [![Play + hint, 390px, dark](play-390-dark.png)](play-390-dark.png) | [![Game review, 390px, dark](review-390-dark.png)](review-390-dark.png) | [![Puzzles, 390px, dark](puzzles-390-dark.png)](puzzles-390-dark.png) | [![Profile, 390px, dark](profile-390-dark.png)](profile-390-dark.png) | [![Settings, 390px, dark](settings-390-dark.png)](settings-390-dark.png) |

## Fixture import provenance

The handoff's prepared file used the ID `m81-live-opening`, which the existing importer rejected as a malformed row because source IDs must be alphanumeric. The replacement fixture changes only that ID to `M81liveOpening`; the start position, continuation and numeric metadata are unchanged. Import accepted and inserted one row with no skips. Compressed fixture SHA-256: `d59ba51ce49b3572a53bc265e30ce90449a9b3320c66410807f84df308ce53c5`. Its single-row catalog intentionally underfills a daily set and is not representative puzzle supply.

## Recorded results

The final run against the source stack above produced 48 images in eight fresh contexts. Every context passed player creation, persisted appearance after reload, a real bot reply and hint, a scored game review, and the imported puzzle continuation. Both bundled fonts loaded; no non-local request hosts were observed. All six screens fit the recorded viewport and available main width. The 48 images were inspected as eight contact sheets.

| Width | Appearance | Screens | Interaction checks | Reflow | Fonts | Non-local hosts |
|---|---|---|---|---|---|---|
| 1440 | Light | 6 | Pass | Pass | Pass | 0 |
| 1440 | Dark | 6 | Pass | Pass | Pass | 0 |
| 1024 | Light | 6 | Pass | Pass | Pass | 0 |
| 1024 | Dark | 6 | Pass | Pass | Pass | 0 |
| 768 | Light | 6 | Pass | Pass | Pass | 0 |
| 768 | Dark | 6 | Pass | Pass | Pass | 0 |
| 390 | Light | 6 | Pass | Pass | Pass | 0 |
| 390 | Dark | 6 | Pass | Pass | Pass | 0 |
