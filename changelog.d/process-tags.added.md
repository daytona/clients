**Process tags** — Group processes, PTYs and shell sessions with `tag`, filter
with `process.list()`, and stop tagged work and its descendants with Python
`process.kill_all()` or TypeScript `process.killAll()`. Tags are reusable after
a kill; macOS supports tags and filtering but not tag-wide kills.
