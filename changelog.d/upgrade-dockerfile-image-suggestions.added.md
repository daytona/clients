**Migration tool: `build_from_context` / `buildFromContext` for 0.x Dockerfile
images** — when a 0.x `snapshot.create()` or `create()` provably passes
`Image.from_dockerfile(...)` / `Image.fromDockerfile(...)` as its image
(directly or through an unchanged binding of the 0.x `Image` class's call),
`--suggest-v2` in both tools suggests building from a context archive with
`images.build_from_context()` / `images.buildFromContext()` instead of an
`ImageBuilder`, with notes on packing the Dockerfile's directory; the migration
guide lists these forms.
