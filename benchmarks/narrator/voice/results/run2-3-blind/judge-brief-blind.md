# Judge brief for blind-items.json

Sent 2026-11-28 12:30 UTC to a new judge session. The text is verbatim, except that local folder paths are removed.

```text
You are a blind judge for a chess-writing experiment. Read blind-items.json. It holds 190 items. Each item has an id, the position in plain words, a list of verified facts, and a short comment written for a club player. Judge every comment against its own position and facts only. Do not read any other file in that folder: the key and the run files would unblind you.

For each comment give:
1. "faithful": true if every statement in the comment is true given the facts and the position; false otherwise. A statement is false if it:
   - gives a fact the wrong role, e.g. says the price of a move is something else;
   - names a wrong side, piece, square or move;
   - adds a chess claim the facts and position do not support, e.g. an advantage, a plan, a danger, "decisive", "material", "development", or a mate that is not in the facts;
   - is nonsense.
   Rhetorical colour is allowed when it restates a fact, e.g. "the knight goes hunting" for a knight that attacks a rook. Judge the chess, not the grammar.
2. "error": when faithful is false, quote the false words and say why in at most 15 words. Otherwise "".
3. "lively": 1, 2 or 3. 1 = a flat restatement of the facts. 2 = some colour or a clear story. 3 = vivid, the way a lively annotator such as Chernev or Tal would write.
4. "clear": 1, 2 or 3. Is it easy for a club player to follow what happened and why? 3 = yes, at once.
5. "jargon": true if it uses chess jargon or abstract terms a club player would not use naturally ("positional", "prophylaxis", "tempo", "structure", numbers for evaluation), else false.

Be strict about faithful: when in doubt, it is false. Judge each item on its own.

Write the results to blind-judgements.json as a JSON list of {"id","faithful","error","lively","clear","jargon"}, one entry per item, all 190. You may write a small script to help with bookkeeping, but the judgements must be your own reading of each comment; no keyword rules. When done, report the count of items judged and the share faithful.
```
