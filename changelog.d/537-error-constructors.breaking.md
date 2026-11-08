**Error fields and constructors** — v2 errors carry the code only as `code`
(`error_code` / `errorCode` are removed). `code` and `source` are open
vocabularies: an unknown code is kept verbatim and the error falls back to the
class for its HTTP status. Error constructors take the message plus options:
TypeScript `new NotFoundError(message, { statusCode, code, source, traceId,
cause, … })` with the exported `ErrorOptions` type, Python
`NotFoundError(message, *, status_code=None, code=None, source=None, …)` (the
Python `ErrorDetails` holder is gone; its fields are keywords). Defaults agree:
`TransferCancelledError()` is "file transfer cancelled" with status 499, code
`REQUEST_CANCELLED` and source `SDK` in both SDKs, and `LimitExceededError`
defaults its code to `LIMIT_EXCEEDED`. `ImageBuildFailedError.buildError` /
`build_error` is the image's recorded build error, when it has one, separate
from `message` (TypeScript used to copy the message into it). TypeScript
`BuildLogInterruptedError` takes `(message, { cursor, code, … })`.
