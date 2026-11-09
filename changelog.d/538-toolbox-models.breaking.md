**Typed toolbox results** — the toolbox results are SDK-owned models in both
SDKs, with every field the API guarantees required: `FileInfo`, `FileMatch`
and `ReplaceResult` (`grep()` / `replace()` items, dictionaries in Python
before), `GitStatus` (with `GitFileStatus` entries), `GitCommit` (`history()`,
dictionaries in Python before), `GitRemote` (`list_remotes()`),
`GitCommitResponse`, `SystemMetrics`, the LSP types (an optional
`LspCompletionItem.documentation`, when present, is a string or an
`LspMarkupContent`, no generated wrapper; completions take a typed
`LspCompletionContext`) and the computer-use results
(`ComputerUseScreenshotResponse.sizeBytes` / `size_bytes` and the seven add-on
process status fields are required; Python `ComputerUseDesktopFailure` is
keyword-only with a required `raw_code`). Python models are frozen
keyword-only dataclasses (they lose the generated pydantic models' methods:
`to_dict()`, `from_dict()`, `to_json()`, `from_json()`, `model_dump()`, …)
whose timestamps are timezone-aware `datetime`s (`ProcessInfo`,
`FileInfo.modified_at`, `GitCommit.timestamp`, `SystemMetrics.timestamp`,
computer-use recordings); TypeScript models are readonly interfaces with RFC
3339 timestamp strings (except the Unix-millisecond numbers `timestampMs` /
`timestamp_ms` of log frames and filesystem watch events, in both SDKs),
frozen at run time at every depth, as are the values a callback receives (a
watch event): a write to a returned result, which the types already refused,
now throws in strict mode (live handles, byte buffers and streams are not
frozen). Unknown top-level response fields are dropped from TypeScript
computer-use results, as in Python (the process status, desktop status and
failure models keep theirs in `extra_fields` / `extraFields`), and the
TypeScript computer-use model arrays are `readonly`.
