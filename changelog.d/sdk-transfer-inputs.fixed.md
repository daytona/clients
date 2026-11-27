**File transfer options are checked before the transfer starts** — Python's
`upload_file()` / `download_file()` refuse a `cancel` without a callable
`is_set()` and a non-callable `on_progress`, and TypeScript's `uploadFile()` /
`downloadFile()` refuse a `signal` that is not an `AbortSignal` (an object with
a boolean `aborted` and `addEventListener` / `removeEventListener`, so a signal
from another realm still works) and a non-function `onProgress`, each with
`BadRequestError` before any request or local file is touched. They used to
fail mid-call with `AttributeError` / `TypeError`. `atomic` on the uploads and
the writes must be a boolean in both SDKs.
