**`create*()` and `fork()` return the sandbox** — once the create or fork
request has succeeded, they return the sandbox however the wait after it ends:
the `wait_seconds` / `waitSeconds` budget runs out, a terminal state is
reached, or a read during the wait fails, unless that read returned a response
that breaks the contract, which is raised as its `BadRequestError`. Check
`sandbox.state` and `sandbox.error`. A raised create or fork means the request
itself failed, with these exceptions: a 2xx response without an `id`, or with
an empty or blank one (`BadRequestError`; the sandbox may exist);
`SandboxCreateRequestTimeoutError` and `SandboxCreateConnectionError`, raised
when the create request timed out or lost its connection, which carry the
idempotency key to retry with because a sandbox may exist; caller
cancellation; and an unexpected non-SDK error during the wait.
`SandboxCreateTimeoutError` is removed, and a server 408 on the create request
is the server's `RequestTimeoutError` in both SDKs. Only the `id` of the
create or fork response is required, in both SDKs: a `status` that is missing
or not a string gives state `unknown`, a fork's `forked_from` falls back to
the source sandbox's id, and any other field that is missing or not a string
(a toolbox URL, a name, a token that could not be sent as a header, an expiry
that is not an RFC 3339 date-time with an offset, a reservation id) is absent,
the toolbox URL then coming from the detail read a wait makes or from
`refresh_data()` / `refreshData()`; only a response without a usable `id` is
an error, as above (TypeScript threw a raw `TypeError` for a non-string create
toolbox URL, and both SDKs refused a fork response without `forked_from` or a
toolbox URL). Likewise `images.build()` / `pull()`, `capture_image()` /
`captureImage()` and `create_snapshot()` / `createSnapshot()` need only the
`id` of their acknowledgement before they read the artifact.
