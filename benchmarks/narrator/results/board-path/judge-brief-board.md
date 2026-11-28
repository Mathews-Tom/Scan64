# Judge brief for board-items.json

Sent 2026-11-28 to one new judge session (Claude Sonnet). The text is verbatim, except that the local folder path is shown as `<judge folder>`. The rubric is the run 9 brief of #458 (docs/evaluation/narrator-voice.md on that branch), adapted to Analysis Board comments: the facts describe the engine's top lines, and no move was played.

```text
You are a blind judge for a chess-writing experiment. Read only <judge folder>/board-items.json: 100 items. Each item has an id, the position in plain words, a list of verified facts, and a short comment written for a club player. The facts describe the chess engine's top lines for the position: an "Opening:" fact, then facts labelled "Line N, <role>:" for each line, where "The move" is the line's first move and "What follows" is the reply and the moves after it. No move was played in the game; the comment explains the lines the engine suggests. Judge every comment only against its own position and facts. Do not open any other file, in that folder or elsewhere.

For each comment give:
1. "faithful": true if every statement is true given the facts and the position, else false. False includes:
   - a fact given to the wrong line or the wrong role;
   - a wrong side, piece, square or move;
   - a chess claim the facts do not support (advantage, danger, decisive, material, development, a mate not in the facts, calling a move forced or necessary when the facts say otherwise);
   - an attacked piece described as taken or captured, or a capture placed before or at the same time as the check that must come first;
   - a move direction that contradicts the facts (for example "back" for a move the facts call "forward");
   - nonsense.
   Rhetorical colour that restates a fact is allowed ("the knight goes hunting" for a knight that attacks a rook). Judge the chess, not the grammar. When in doubt, false.
2. "error": when faithful is false, quote the false words and say why in at most 15 words; else "".
3. "lively": 1 = a flat restatement of the facts, 2 = some colour or a clear story, 3 = vivid, the way a lively annotator such as Chernev or Tal writes.
4. "clear": 1-3. Can a club player follow what happened and why? 3 = yes, at once.
5. "jargon": true if it uses chess jargon or abstract terms a club player would not use naturally ("positional", "prophylaxis", "tempo", "structure", evaluation numbers).

Judge each item on its own reading; no keyword rules. You may use a small script only for bookkeeping (reading the file, writing the output). Write <judge folder>/board-judgements.json as a JSON list of {"id","faithful","error","lively","clear","jargon"}, one entry per item, all 100, with faithful and jargon as true/false and lively and clear as the integers 1, 2 or 3. Report the count judged and the share faithful.
```
