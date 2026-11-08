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
defaults its code to `LIMIT_EXCEEDED`; a field passed as `None` / `undefined`
takes its default too. The client's own timeout,
`ClientTimeoutError`, has one shape on every request path in both SDKs:
status 408, code `REQUEST_TIMEOUT`, source `SDK`, and by default the message
"operation timed out" (the build-log readers say which read timed out) (it
used to carry no fields in Python and on TypeScript's axios path), and
`ConnectionError` carries source `SDK`. A TypeScript create POST that hits the
request timeout now raises `SandboxCreateRequestTimeoutError` with its
idempotency key, as Python does, and a server 408 on it stays a
`RequestTimeoutError`. `ImageBuildFailedError.buildError` /
`build_error` is the image's recorded build error, when it has one, separate
from `message` (TypeScript used to copy the message into it). TypeScript
`BuildLogInterruptedError` takes `(message, { cursor, code, … })`, and
`SandboxTerminalStateError` takes `(sandboxId, state, { rawState })` (Python
`raw_state=` is keyword-only; `rawState` was positional). A TypeScript
error constructor given a second argument that is not an options object
(`new NotFoundError('x', 404)` from plain JavaScript) throws `BadRequestError`
instead of dropping it, and reads every enumerable field of the options,
inherited ones included, as the client configuration does. Errors the TypeScript SDK raises itself now carry the
catalog's class, as in Python: an invalid argument (a negative timeout, a
blank name or create source, an invalid image step, ...) is a `BadRequestError` (it was the base
`DaytonaError` with status 400), so `instanceof BadRequestError` and the root
`DaytonaBadRequestError` catch it; an interpreter run that times out creating
its isolated context is a `RequestTimeoutError`; an invalid project-token
exchange response is a `BadRequestError` (400, `BAD_REQUEST`) in both SDKs
(it was an `AuthenticationError` without status or code in TypeScript); an
image `timeout` that is negative or not finite is a `BadRequestError` instead of
a `TypeError` in TypeScript, and so is a build-log `timeout` in both SDKs
(it was a `RangeError` / `ValueError`).
