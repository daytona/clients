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
returned an empty list. Unknown top-level response fields are dropped from
TypeScript computer-use results, as in Python (the process status, desktop
status and failure models keep theirs in `extra_fields` / `extraFields`), and
TypeScript rejects a negative value where the API's integer has a minimum of
0, as Python does. TypeScript `listBranches()` / `listRemotes()` accept only
the API's `{ branches }` / `{ remotes }` shapes, and the computer-use model
arrays are `readonly`. Both SDKs check `git.status()`'s `fileStatus` list, the
computer-use `start()` / `stop()` status map, a `get_config()` / `getConfig()`
value (a string or null) and its `scope` (`'global'` or `'local'`; Python
gains the `GitConfigScope` type), the error parts of `read_many()` /
`readMany()` (both require a string `path` and `message`, which were filled in
with empty strings, and leave the optional `code` / `source` absent / `None`
when not sent; a part of any other type, or that is not valid UTF-8 JSON, is
refused, as is a file part whose `filename*` is not valid percent-encoded
UTF-8), a window id up to 2^53 - 1 and a `cpuUtilization` between 0 and 1, an
RFC 3339 timestamp with the same field ranges (hour 00-23, no leap second,
offset within ±23:59), a created process record's non-empty `id`, the record a
timed-out `wait()` re-reads, in full (Python checked only its generated model,
so an empty `id` or a bad `createdAt` still returned a result), every list,
map and nested-object field of a response before Python decodes it (Python
read a `{}` or `""` list as empty, so a `run()` whose inline log `frames` was
malformed returned no output, and raised a raw `TypeError` for a number; this
covers `run()` / `exec()` / `session.run()` logs, the display, window and
recording lists and accessibility nodes), every number field a double can
carry (Python accepted an integer beyond the float range, such as `1` followed
by 400 zeros, which TypeScript reads as `Infinity`, and raised a raw
`OverflowError` for a recording `durationSeconds` or process `timeoutMs`; it
now refuses it naming the field, "must be an integer" / "must be a number", in
process records, log frames and stream events, watch events, file info, git,
LSP and computer-use results; integers above 2^53 that a double carries stay
accepted), and the interpreter session responses for the fields they read
(`id`, `kernel`; a missing or empty `id` names a context id the caller chose,
which may have been created), and `glob()` accepts only the API's `{ files }`
response. A process log stream or watch skips an event it does not know
(whatever its data) and raises the error for a known event whose data is not
JSON; Python ended the stream with a `JSONDecodeError`. `mkdir()` defaults to
mode `"0755"` in both SDKs. TypeScript `readText()` decodes strictly as UTF-8
and keeps a leading byte-order mark, like Python; `WriteFileOptions.mode` is
removed (use `chmod()`), as are the aliases `Match`, `SearchFilesResponse`,
`FilePermissionsParams` and `ListBranchResponse`.
