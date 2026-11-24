# Community lesson packs

In **Library → Community lesson packs**, choose a local JSON file. Scan64 uploads the original bytes to your local server, checks the schema and verifies each lesson with the shipped engine. There is no remote catalogue, registry, download URL, author signature or executable content. Pack text is displayed as plain text, never HTML. Only the importing player can access or uninstall a pack.

Pending and verifying packs refresh automatically. Title, author, licence, content version, short SHA-256 and installation date identify the imported file; hover over the short hash for its full value. Provenance is an author's declaration, not an endorsement. Rejected lessons show their first failing ply (1 = the first move of the line) and reason and cannot be opened. **Retry** is available for failed or unavailable verification, for example after restoring a usable engine. Unavailable packs hide their lessons. Verification must finish before a lesson can open.

Open a verified lesson, read its objective and play on the board or use its keyboard move form (UCI or exact English SAN). Moves and replies are checked by the server against the verified pack continuation. **Show hint** asks the server for hints without changing position. The explanation appears after completion. These are community lessons, not positions from your own games. Playing them records no attempts, review schedules, skill changes, recurrence or diagnosis evidence.

**Uninstall** requires confirmation and removes the pack and its lessons. Deleting the player's data also removes their packs. Player archive exports **exclude community packs**: keep the original JSON and reinstall it after restoring an archive; all lessons are verified again. To replace any installed pack version with the same id, uninstall first.

## JSON format

See [the small example file](lesson-packs-example.json). It intentionally contains one sound mate-in-one and one legal but unsound stalemate continuation, so the latter should be rejected.

The top-level object has these exact fields:

| Field | Value |
| --- | --- |
| `format` | Literal `"scan64-lesson-pack"` |
| `format_version` | Integer `1`, the schema version |
| `id` | Unique slug identifying the pack |
| `title`, `author`, `licence` | Plain-text provenance; use an explicit licence identifier |
| `version` | String identifying the author's content version, separate from `format_version` |
| `description`, `source` | Optional plain-text strings, not fetched URLs |
| `lessons` | Array of lesson objects |

Each lesson requires `id` (a unique slug), `title`, standard-chess `fen`, `solution` (an array of UCI move strings, including promotion suffix when needed), `objective`, `hints` (string array), `explanation`, and `themes` (string array). Slugs use lowercase letters/digits separated by hyphens. Unknown fields, type coercion and duplicate ids are rejected.

Limits: 512 KiB for the original file, 50 lessons, at most 5 plies per solution (an odd number, ending on the solver's move), 3 hints per lesson, and 2,000 characters per string. Control characters are forbidden; newline is allowed only in long-form text. Use ordinary strings rather than markup. Invalid FEN rejects the entire file; illegal solution lines reject the affected lesson. At every solver ply the pack move must be among the shipped verifier's sound moves, not merely legal. A file rejected during schema validation changes nothing.
