# Judge brief for blindm1-items.json

Sent 2026-11-28 13:33 UTC to a new judge session. The text is verbatim, except that local folder paths are removed.

```text
You are a blind judge for a chess-writing experiment. Read only blindm1-items.json: 300 items. Each item has an id, the position in plain words, a list of verified facts (with role labels such as "The move:", "What it misses:", "What White could have played:"), and a short comment written for a club player. Judge every comment only against its own position and facts. Do not open any other file in that folder; the key and run files would unblind you.

For each comment give:
1. "faithful": true if every statement is true given the facts and the position, else false. False includes:
   - a fact given the wrong role (e.g. calling an advantage "the price");
   - a wrong side, piece, square or move;
   - a chess claim the facts do not support (advantage, danger, decisive, material, development, a mate not in the facts, calling a move forced or necessary when the facts say otherwise);
   - an attacked piece described as taken or captured, or a capture placed before or at the same time as the check that must come first;
   - "!" after a move the facts present as a mistake;
   - nonsense.
   Rhetorical colour that restates a fact is allowed ("the knight goes hunting" for a knight that attacks a rook). Judge the chess, not the grammar. When in doubt, false.
2. "error": when faithful is false, quote the false words and say why in at most 15 words; else "".
3. "lively": 1 = a flat restatement of the facts, 2 = some colour or a clear story, 3 = vivid, the way a lively annotator such as Chernev or Tal writes.
4. "clear": 1–3. Can a club player follow what happened and why? 3 = yes, at once.
5. "jargon": true if it uses chess jargon or abstract terms a club player would not use naturally ("positional", "prophylaxis", "tempo", "structure", evaluation numbers).

Judge each item on its own reading; no keyword rules. You may use a small script only for bookkeeping. Write blindm1-judgements.json as a JSON list of {"id","faithful","error","lively","clear","jargon"}, one entry per item, all 300. Report the count judged and the share faithful.
```
