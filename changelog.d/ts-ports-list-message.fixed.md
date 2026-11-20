**`ports.list()` / `ports.isInUse()` on a handle without a toolbox URL** —
TypeScript now says to fetch the sandbox with `get(id)` (or pass `toolboxUrl`),
matching Python's message and the other toolbox namespaces, instead of
"toolbox transport is not available for sandbox port inspection".
