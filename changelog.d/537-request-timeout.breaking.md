**Default request timeout: 60 seconds, total time** — both SDKs now bound
each ordinary call by 60 seconds by default (it was 30 s per I/O phase in
Python and 24 hours in TypeScript), and the timeout means the same in both:
one deadline for the whole call, from resolving the credential (a token
exchange) through the request, a 401 refresh and its retry, to the last byte
of the response, ending in `ClientTimeoutError`. Requests governed by another
budget are not cut by it: waits and long polls use their own deadline
(in Python too that deadline is now total time, like TypeScript's, instead of a
per-phase httpx timeout, and on a stream it bounds opening it, as the request
timeout does; Python's image build and pull submissions, `POST /images`,
which had no time limit, now use the request timeout, as in TypeScript),
streams (process logs, file watches, build logs, events) are bounded only
until their response headers arrive, and file transfers (uploads, downloads
and the bulk read / write calls) have no total limit but are idle-bounded by
it (see the transfer-bounds entry). `0` disables it, and so
does any value above 2,147,483.647 seconds (about 24.8 days, the longest
timer JavaScript can arm); a negative or non-finite value is a
`BadRequestError`. TypeScript: the root option `requestTimeout`
(milliseconds) is renamed `requestTimeoutSeconds` (seconds); Python keeps
`request_timeout` (seconds). The events hub durations (`reconcile_interval` /
`idle_close`, TypeScript `reconcileIntervalSeconds` / `idleCloseSeconds`) are
validated the same way, but a value above the timer maximum clamps to it
(less 10 % for the reconcile interval, which is jittered) instead of meaning
no limit, in both SDKs. Python's `daytona.v2.http.HttpClient`, a blocking
transport the clients never used, whose timeout was per I/O phase, is no
longer exported.
