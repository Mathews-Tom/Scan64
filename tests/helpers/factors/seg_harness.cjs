// Parity harness for the Stockfish Evaluation Guide (hxim, GPL-3.0).
//
// Usage: node seg_harness.cjs <guide index.html> <positions.json> [out.json] [start] [end]
//
// start and end select a slice of the positions, so callers can run shards in
// parallel (the guide code is slow: about one second per position).
//
// It reads the guide's own JavaScript out of index.html (the board helpers and
// every term stored as data.push({name, group, code})), runs it in a Node vm
// context, and prints the whole-board value of every term for each position,
// for White (the position) and for Black (colorflip of the position).
// Run it on a copy of the guide outside the repository tree.
"use strict";
const fs = require("fs");
const vm = require("vm");

const [htmlPath, positionsPath, outPath, start, end] = process.argv.slice(2);
if (!htmlPath || !positionsPath) {
  console.error("usage: node seg_harness.cjs <index.html> <positions.json> [out.json]");
  process.exit(2);
}
const html = fs.readFileSync(htmlPath, "utf8");

function extractFunction(source, name) {
  const start = source.indexOf("function " + name + "(");
  if (start < 0) throw new Error("helper not found: " + name);
  let depth = 0;
  for (let i = source.indexOf("{", start); i < source.length; i++) {
    if (source[i] === "{") depth++;
    else if (source[i] === "}" && --depth === 0) return source.slice(start, i + 1);
  }
  throw new Error("unbalanced helper: " + name);
}

const helpers = ["bounds", "board", "colorflip", "sum", "parseFEN"]
  .map((n) => extractFunction(html, n))
  .join("\n");

const terms = [];
for (const line of html.split("\n")) {
  const t = line.trim();
  if (t.startsWith('data.push({"name"')) {
    terms.push(JSON.parse(t.slice("data.push(".length, t.lastIndexOf(")"))));
  }
}

const context = vm.createContext({});
vm.runInContext(helpers, context);
const ids = terms.map((d) => "$" + d.name.toLowerCase().replace(/ /g, "_"));
terms.forEach((d, i) => vm.runInContext(ids[i] + " = " + d.code + ";", context));

const positions = JSON.parse(fs.readFileSync(positionsPath, "utf8")).positions.slice(
  start ? Number(start) : 0,
  end ? Number(end) : undefined,
);
const results = [];
for (const p of positions) {
  context.__fen = p.fen;
  const white = {};
  const black = {};
  terms.forEach((d, i) => {
    context.__f = context[ids[i]];
    white[d.name] = vm.runInContext("__f(parseFEN(__fen))", context);
    black[d.name] = vm.runInContext("__f(colorflip(parseFEN(__fen)))", context);
  });
  results.push({ id: p.id, fen: p.fen, white, black });
}

const out = JSON.stringify({
  terms: terms.map((d) => ({ name: d.name, group: d.group })),
  results,
});
if (outPath && outPath !== "-") fs.writeFileSync(outPath, out + "\n");
else process.stdout.write(out + "\n");
