**Registry references with whitespace are refused** —
`ImageBuilder.from_registry()` / `ImageBuilder.fromRegistry()` refuse a
reference with whitespace inside it (`"python 3"`) with `BadRequestError`, and
in Go `NewImageBuilder()` records it and `Dockerfile()` / `Build()` return an
error matching `ErrBadRequest`, instead of rendering an invalid
`FROM python 3`.
Surrounding whitespace is still trimmed, and references with a registry port,
a tag or a digest are unchanged.
