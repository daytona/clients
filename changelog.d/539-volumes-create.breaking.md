**Volumes: `create()` takes options** — TypeScript `volumes.create(name,
sizeGib)` becomes `volumes.create({ name, sizeGib, type })` (`sizeGib` a
positive integer), as in Python's `create(name=..., size_gib=..., type=...)`.
`type` (`disk` or `bucket`) is optional: an omitted type is chosen by the
server.
