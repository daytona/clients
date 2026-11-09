**Toolbox responses are checked** — a toolbox response that breaks the
contract (a missing required field or list, a wrong type, a fractional
integer, a malformed watch event) raises `BadRequestError` (400,
`BAD_REQUEST`, source `SDK`) naming the field, instead of being filled with an
empty value. The checks include, in both SDKs:

- `git.status()`'s `fileStatus` list; a `get_config()` / `getConfig()` value
  (a string or null) and its `scope` (`'global'` or `'local'`; Python gains the
  `GitConfigScope` type); `list_branches()` / `listBranches()`,
  `list_remotes()` / `listRemotes()` and `glob()`, which accept only the API's
  `{ branches }`, `{ remotes }` and `{ files }` shapes (Python
  `list_remotes()` and `glob()` returned an empty list).
- The error parts of `read_many()` / `readMany()`: `path` and `message` must
  be strings (they were filled in with empty strings); `code` and `source` are
  optional; a part that is not a JSON object in valid UTF-8, or a file part
  whose `filename*` is not valid percent-encoded UTF-8, is refused.
- Process records: a created record's non-empty `id`, and the record a
  timed-out `wait()` re-reads, in full (Python checked only its generated
  model, so an empty `id` or a bad `createdAt` still returned a result).
- Every list, map and nested-object field before Python decodes it: Python
  read a `{}` or `""` list as empty (a `run()` whose inline log `frames` was
  malformed returned no output) and raised a raw `TypeError` for a number;
  this covers `run()` / `exec()` / `session.run()` logs, the display, window
  and recording lists and accessibility nodes.
- The computer-use parsers (they raised a 500 `InternalServerError`), the
  `start()` / `stop()` status map, a window id up to 2^53 - 1 and a
  `cpuUtilization` between 0 and 1.
- The interpreter session responses, for the fields they read (`id`,
  `kernel`; a missing or empty `id` names a context id the caller chose, which
  may have been created).
- A negative value where the API's integer has a minimum of 0 (TypeScript
  accepted one; Python already refused it).

The API's pre-signed file URL response is checked the same way (it raised a
500 `InternalServerError`).
