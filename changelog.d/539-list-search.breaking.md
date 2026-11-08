**`list()` and `search()`** — the client's `list()` and `iter()` take only a
cursor and a limit and always read the read-your-writes sandbox list; filters
moved to `search()`. `search()` takes `states` with SDK sandbox states
(`["started", "paused"]`) instead of the API's `status` values; `"unknown"` is
refused (and excluded from the type: Python `SearchSandboxState`). The
`ListSandboxesQuery` export is removed from both SDKs: Python's
`ListSandboxesQuery` / `SearchSandboxesQuery` are renamed
`ListSandboxesOptions` / `SearchSandboxesOptions`, TypeScript uses
`ListSandboxesOptions` / `SearchSandboxesOptions`, and it exports
`FindByNameOptions`. `ListResult` no longer has `total` /
`total_relation` / `raw_total_relation` (`totalRelation` / `rawTotalRelation`);
`SearchResult` keeps them. List and search pages are immutable: Python `items`
is a tuple, TypeScript fields are `readonly` with `readonly T[]` items.
`find_by_name()` / `findByName()` still returns every match.
A list response that breaks its page shape (no array, a non-object entry, a
non-string `next_cursor`) is a `BadRequestError` naming the field in
TypeScript too, for sandboxes, images, snapshots, volumes, secrets,
reservations, Mac models, preview endpoints and ports; it used to throw a raw
`TypeError` (a volume list without `volumes` read as empty).
