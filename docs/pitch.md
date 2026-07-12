# Scan64 — Pitch

*A one-pager to test the idea with colleagues. Full detail: [`system-overview.md`](./system-overview.md) and [`system-design.md`](./system-design.md).*

---

## The idea in one sentence

**Scan64 plays chess with you, figures out what you keep failing to see, and builds you verified exercises that fix it — as a complete open-source app, on top of an open, headless learning engine other developers can build on.**

## The problem

Every chess app can tell you *that* you blundered. Almost none can tell you *why you keep blundering the same way*.

Stockfish will tell a player their move dropped the evaluation from +0.3 to −1.8. It won't tell them they didn't scan for opponent threats before continuing their own attack — again — for the fourth time this month, in four different-looking positions. That gap between "engine analysis" and "why I keep failing" is the whole product.

## The insight

> A player's own game history is already a personalized curriculum. It just needs to be read correctly.

Engine analysis finds the important moments. It cannot, by itself, tell you *what skill failed* or *whether it's recurring* — that needs a separate layer that connects mistakes across games, distinguishes a one-off slip from a pattern, and turns the pattern into practice.

## What we'd build

1. **A complete, free, local-first chess app** — computer play (human-like opponents via Maia, not a robotic weakened engine), analysis, openings, tactics, endgames, famous games, structured training. This is the acquisition surface and the evidence source, not the differentiator by itself.
2. **A personalized learning engine underneath it** that:
   - Diagnoses *why* a mistake happened, not just that it happened, with confidence and evidence attached to every claim.
   - Builds a multidimensional weakness/habit profile — better than a single Elo number.
   - Generates verified exercises from the player's *own* mistakes, including "transfer" versions that change the surface of the position but keep the underlying idea, so success proves recognition rather than memorization.
   - Schedules review with spaced repetition and reports progress in plain language.
3. **A published, versioned lesson format (`LessonSpec`)** so any third-party app — mobile, classroom, voice, physical board — can plug into the same diagnosis engine without adopting our UI.

Stockfish supplies chess truth. Maia supplies realistic opposition. An *optional* LLM supplies language, never chess facts — every core function works with zero LLM calls. Our own contribution is the layer that turns evidence into an individualized curriculum.

## Why now

- Chess.com alone has gone from ~44M to 268.6M members since 2020; Lichess (free, open-source) still pulls ~65–80M site visits a month. The audience is large and has repeatedly shown it responds to the right hook (app downloads rose 63% after *The Queen's Gambit* aired).
- The category already monetizes *training specifically*: Chessable and Aimchess were both acquired into Chess.com (now >$1B valued, freshly backed by CVC Capital Partners) — proof players pay for structured improvement, not just play.
- The two hardest dependencies are free and mature now in a way they weren't a few years ago: Stockfish is state-of-the-art open source, and Maia/Maia-2 give a genuinely human-like opponent policy (not a dumbed-down engine).
- Nobody has open-sourced the *diagnosis-and-personalization layer itself*. Aimchess personalizes but is closed and aggregate-stats-based. Chessable is closed and content-authored, not derived from your own games. Lichess is open but has no personalization layer at all.

## Why we can win the differentiation, even against a >$1B incumbent

| | Aimchess (closed, in Chess.com) | Chessable (closed, in Chess.com) | Scan64 |
| --- | --- | --- | --- |
| Diagnosis | Aggregate stats | N/A (authored courses) | Evidence-linked, per-position, verified |
| Exercises | Generic drills | Pre-authored | Generated from *your* games, verified |
| Success metric | Engagement / accuracy | Course completion | Measured transfer to future games |
| Openness | Closed SaaS | Closed SaaS | Open core, public API, portable lesson format |

We're not trying to out-build Stockfish, out-build Maia, or out-author Chessable's course library. We're building the one layer none of them ship: verified, individualized, transfer-tested diagnosis — in the open.

## The catch, stated plainly

The hard part is exactly the differentiated part: reliably inferring *why* a specific player failed, telling a one-off slip from a real pattern, and proving training actually reduces future mistakes rather than just improving puzzle-accuracy vanity metrics. That's a genuinely hard, currently-unsolved-in-the-open problem — which is also why it's worth doing. The plan doesn't assume it works: the first milestone is a narrow proof — take real games, find a recurring failure with inspectable evidence, generate a lesson, and later show recognition of the same idea in a different position — before any app gets built around it.

## What "done" looks like at each stage

- **Prove the loop** (no app yet): CLI + Python package only. Feed it real games (starting with the creator's own). Does it find a real recurring weakness, with evidence a coach would agree with, and generate a legitimate exercise from it?
- **Vertical slice**: minimal but production-shaped board UI, rendering hints exclusively from the public `LessonSpec` contract — proves the play-to-lesson pipeline works end to end through public interfaces, not shortcuts.
- **Complete v1**: the full app — play, analysis, openings, tactics, endgames, famous games, daily adaptive training — usable without any personalization data yet, and better with it.
- **Validated**: real users, pre/post measurement showing recognition of a trained concept transfers to unseen positions — the actual claim this whole thing rests on.

## License and governance stance

AGPL-3.0-or-later, deliberately: it lets anyone use, modify, and even sell hosting around Scan64, but if they run a modified version as a network service, their improvements to the shared engine have to stay available to their users too. Open enough to build an ecosystem on; not open enough for a closed competitor to fork the differentiated layer into a proprietary product.

## What we're testing with this pitch

Not asking for a commitment — asking whether the thesis survives contact with people who'd use or build on it:

1. Does "diagnose *why*, not just *that*" feel like a real gap to you, or does existing engine review already cover it for how you play/study?
2. Would you use a chess app whose main claim is *measured improvement*, if the roadmap up front says that claim is unproven until Phase 3?
3. If you build tools/bots/trainers: would a public, versioned lesson format be something you'd actually build against, or is "yet another API" not worth the switching cost?
4. What would make you personally distrust an automated diagnosis of your own play — and does the evidence-linked, confidence-gated design above address that, or is there a gap?

Full risk register, market data, and moat analysis: [`system-overview.md`](./system-overview.md). Full architecture and workflow diagrams: [`system-design.md`](./system-design.md).
