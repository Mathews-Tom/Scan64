# chess-lesson-spec

Portable Pydantic models for the versioned Scan64 `LessonSpec` protocol.


The package is consumed by Scan64's web application and can also support independent renderers. See the repository [system design](../../docs/system-design.md) for the delivered learning-loop boundary and the [user guide](../../docs/user-guide.md) for application workflows.
The package is distributed under `GPL-3.0-or-later`, matching the repository's shipped license. It defines the data contract only; it does not render lessons or embed Scan64 application code.

## Workspace contract 0.2.0

The workspace package and generated `schemas/lesson-spec.schema.json` support
multi-move external lessons. This workspace bump is not a package publication.
`interaction.solution` is an ordered UCI line starting at `source.fen`, with solver
moves alternating with automatic replies; promotion includes the exact piece.
Imported puzzles use a post-setup FEN and end their remaining line on a solver move.

`source.kind="player_game"` still requires a diagnosis and engine verification.
External lessons may omit diagnosis and mastery and use
`verification={"status": "source_verified"}` without engine fields. Source
verification means legal source-line validation, not engine certification or a
mastery claim. Scan64's verifier checks line legality and solver-turn parity.
The original setup move and import provenance remain the importer's responsibility.

Stored 0.1.0 lessons remain readable and trainable with their original single-move
`accepted_moves`; new exact-replay and transfer lessons emit 0.2.0. The bundled
fixtures include an unchanged 0.1.0 example and 0.2.0 player-game, external
multi-move and exact-promotion examples.

## Conformance runner

Install the package and run:

```text
scan64-conformance run --renderer /path/to/renderer-adapter
```

The renderer directory must contain `renderer.py` with:

```python
def render_visualization(command: dict[str, object]) -> None:
    ...
```

The runner validates every published `LessonSpec` fixture, then invokes this function for every visualization command in each fixture. Raise `NotImplementedError` for an unsupported command. A non-zero exit means the adapter is nonconformant or failed while handling a fixture.

## Protocol boundary

An independent client can implement this adapter and the published `LessonSpec` JSON protocol without copying or linking Scan64 code. That technical boundary does not determine legal obligations; assess the applicable licence and distribution model independently. This SDK itself is GPL-licensed.
