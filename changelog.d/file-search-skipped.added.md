**Skipped paths in file searches** — `fs.grep()` and `fs.glob()` return a
`FileSearchResult`, a `ListResult` with a new `skipped` field (Python tuple of
str, TypeScript `readonly string[]`): the absolute paths below the root that
the sandbox user cannot read, which were not searched (at most 1000). Only an
unreadable root raises `FileAccessDeniedError`. Responses from sandboxes that
do not report `skipped` yet read as nothing skipped.
