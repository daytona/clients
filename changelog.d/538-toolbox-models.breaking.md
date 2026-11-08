**Typed toolbox results** — the toolbox results are SDK-owned models in both
SDKs, with every field the API guarantees required: `FileInfo`, `FileMatch`
and `ReplaceResult` (`grep()` / `replace()` items, dictionaries in Python
before), `GitStatus` (with `GitFileStatus` entries), `GitCommit`
(`history()`, dictionaries in Python before), `GitRemote` (`list_remotes()`),
`GitCommitResponse`, `SystemMetrics`, the LSP types (`LspCompletionItem.
documentation` is a string or an `LspMarkupContent`, no generated wrapper;
completions take a typed `LspCompletionContext`) and the computer-use results
(`ComputerUseScreenshotResponse.sizeBytes` / `size_bytes` and the seven
add-on process status fields are required; Python
`ComputerUseDesktopFailure` is keyword-only with a required `raw_code`).
Python models are frozen keyword-only dataclasses whose timestamps are
timezone-aware `datetime`s (`ProcessInfo`, `FileInfo.modified_at`,
`GitCommit.timestamp`, `SystemMetrics.timestamp`, computer-use recordings);
TypeScript models are readonly interfaces with RFC 3339 timestamp strings. A
response missing a required field raises `BadRequestError` instead of being
filled with an empty value. TypeScript `readText()` decodes strictly as UTF-8
and keeps a leading byte-order mark, like Python; `WriteFileOptions.mode` is
removed (use `chmod()`), as are the aliases `Match`, `SearchFilesResponse`,
`FilePermissionsParams` and `ListBranchResponse`.
