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
3339 timestamp strings. A response that breaks the contract (a missing
required field or list, a wrong type, a fractional integer, a malformed
timestamp or watch event) raises `BadRequestError` (400, `BAD_REQUEST`, source
`SDK`) naming the field (a present field of the wrong type "must be" the
expected type) instead of being filled with an empty value; this includes the
computer-use parsers and the pre-signed URL response, which raised a 500
`InternalServerError` before, and Python `list_remotes()` / `glob()`, which
returned an empty list. Unknown response fields are dropped from TypeScript
computer-use results, as in Python, and TypeScript rejects a negative value
where the API's integer has a minimum of 0, as Python does. TypeScript
`listBranches()` / `listRemotes()` accept only the API's `{ branches }` /
`{ remotes }` shapes, and the computer-use model arrays are `readonly`. A
process log stream or watch skips an event it does not know (whatever its
data) and raises the error for a known event whose data is not JSON; Python
ended the stream with a `JSONDecodeError`. `mkdir()` defaults to mode `"0755"`
in both SDKs. TypeScript `readText()` decodes strictly as UTF-8 and keeps a
leading byte-order mark, like Python; `WriteFileOptions.mode` is removed (use
`chmod()`), as are the aliases `Match`, `SearchFilesResponse`,
`FilePermissionsParams` and `ListBranchResponse`.
