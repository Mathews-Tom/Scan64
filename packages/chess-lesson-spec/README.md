# chess-lesson-spec

Portable Pydantic models for the versioned Scan64 `LessonSpec` protocol.

The package is distributed under `GPL-3.0-or-later`, matching the repository's shipped license. It defines the data contract only; it does not render lessons or embed Scan64 application code.

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
