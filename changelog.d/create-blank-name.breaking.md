**A blank sandbox name is refused** — `create()`, `create_from_snapshot()` /
`createFromSnapshot()`, `create_from_build()` / `createFromBuild()` and
`create_macos()` / `createMacos()` raise `BadRequestError` ("name must be a
non-empty string when given") for an empty or whitespace-only name (Python's
whitespace set in both SDKs) instead of treating it as no name, as `fork()` and
`find_by_name()` / `findByName()` already did; omit `name` for a generated one.
