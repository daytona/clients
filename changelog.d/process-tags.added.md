**Process tags** — Group processes, PTYs and shell sessions with `tag`, filter
with `process.list()`, and stop tagged work and its descendants with Python
`process.kill_all()` or TypeScript `process.killAll()`. Tags are reusable after
a kill. Tag-wide kills need process containment: on macOS and on sandboxes
without it, tags and filtering work but the kill raises
`UnsupportedOperationError`.
