# Judge brief for blind-items.json

Sent to one new Claude Sonnet session, verbatim except for the folder path and the line that introduced it.

```text
You are a blind judge for a chess-writing experiment. Read only blind-items.json: 185 items. Each item has an id, the position in plain words (before the move that is explained), a list of verified facts with role labels ("The cause:", "The turning point:", "The move:", "What it misses:", "What follows:", "What White/Black could have played:"), and a short comment written for a club player. Judge every comment only against its own position and facts. Do not open any other file outside that folder; the key and run files would unblind you.

For each comment give:
1. "faithful": true if every statement is true given the facts and the position, else false. False includes:
   - a fact given the wrong role (e.g. calling an advantage "the price");
   - a wrong side, piece, square or move;
   - a chess claim the facts do not support (advantage, danger, decisive, material, development, a mate not in the facts, calling a move forced or necessary when the facts say otherwise);
   - an attacked piece described as taken or captured, or a capture placed before or at the same time as the check that must come first;
   - "!" after a move the facts present as a mistake;
   - nonsense.
   Rhetorical colour that restates a fact is allowed ("the knight goes hunting" for a knight that attacks a rook). Leaving a fact out is not unfaithful. Judge the chess, not the grammar. When in doubt, false.
2. "error": when faithful is false, quote the false words and say why in at most 15 words; else "".
3. "lively": 1 = a flat restatement of the facts, 2 = some colour or a clear story, 3 = vivid, the way a lively annotator such as Chernev or Tal writes.
4. "clear": 1-3. Can a club player follow what happened and why? 3 = yes, at once.
5. "jargon": true if it uses chess jargon or abstract terms a club player would not use naturally ("positional", "prophylaxis", "tempo", "structure", evaluation numbers).
6. "root_cause_named": true if a club player learns from the comment what lasting weakness "The cause:" fact states (for example the uncovered king, the isolated pawn), in any words; false if the comment leaves it out or blurs it so the kind of weakness cannot be recognised. null if the item has no "The cause:" fact.
7. "turning_point_named": true if the comment says that the game turned with the move of "The turning point:" fact; only naming that move as the move played is false. null if the item has no "The turning point:" fact.
8. "order": true if the comment first tells the cause (when the item has one), then the turning point (when the item has one), then the move and its line; false if one of them is missing or comes later. null if the item has neither a "The cause:" nor a "The turning point:" fact.

Judge each item on its own reading; no keyword rules. You may use a small script only for bookkeeping. Write blind-judgements.json, in the same folder, as a JSON list of {"id","faithful","error","lively","clear","jargon","root_cause_named","turning_point_named","order"}, one entry per item, all 185. Report the count judged and the share faithful.
```
