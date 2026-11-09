**Bulk read error parts are checked** — in the error parts of `read_many()` /
`readMany()`, `path` and `message` must be strings (they were filled in with
empty strings) and `code` / `source` are optional; a part that is not a JSON
object in valid UTF-8, or a file part whose `filename*` is not valid
percent-encoded UTF-8, is refused with `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`), in both SDKs.
