**Toolbox responses are checked** — a toolbox response that breaks the
contract (a missing required field or list, a wrong type, a fractional
integer, a malformed watch event) raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field (a present field of the wrong
type "must be" the expected type) instead of being filled with an empty value;
this includes the computer-use parsers and the pre-signed URL response, which
raised a 500 `InternalServerError` before, and Python `list_remotes()` /
`glob()`, which returned an empty list. TypeScript rejects a negative value
where the API's integer has a minimum of 0, as Python does; TypeScript
`listBranches()` / `listRemotes()` accept only the API's `{ branches }` / `{
remotes }` shapes, and `glob()` only its `{ files }` response. Both SDKs check
`git.status()`'s `fileStatus` list, the computer-use `start()` / `stop()`
status map, a `get_config()` / `getConfig()` value (a string or null) and its
`scope` (`'global'` or `'local'`; Python gains the `GitConfigScope` type), the
error parts of `read_many()` / `readMany()` (both require a string `path` and
`message`, which were filled in with empty strings, and leave the optional
`code` / `source` absent / `None` when not sent; a part of any other type, or
that is not valid UTF-8 JSON, is refused, as is a file part whose `filename*`
is not valid percent-encoded UTF-8), a window id up to 2^53 - 1 and a
`cpuUtilization` between 0 and 1, a created process record's non-empty `id`,
the record a timed-out `wait()` re-reads, in full (Python checked only its
generated model, so an empty `id` or a bad `createdAt` still returned a
result), every list, map and nested-object field of a response before Python
decodes it (Python read a `{}` or `""` list as empty, so a `run()` whose
inline log `frames` was malformed returned no output, and raised a raw
`TypeError` for a number; this covers `run()` / `exec()` / `session.run()`
logs, the display, window and recording lists and accessibility nodes), and
the interpreter session responses for the fields they read (`id`, `kernel`; a
missing or empty `id` names a context id the caller chose, which may have been
created).
