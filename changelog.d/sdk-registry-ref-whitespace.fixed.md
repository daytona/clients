**Registry references with whitespace are refused** —
`ImageBuilder.from_registry()` / `ImageBuilder.fromRegistry()` /
`NewImageBuilder()` refuse a reference with whitespace inside it (`"python 3"`)
with `BadRequestError`, instead of rendering an invalid `FROM python 3`.
Surrounding whitespace is still trimmed, and references with a registry port,
a tag or a digest are unchanged.
