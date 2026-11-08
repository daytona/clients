**Default request timeout: 60 seconds, total time** — both SDKs now bound
each ordinary request by 60 seconds by default (it was 30 s per I/O phase in
Python and 24 hours in TypeScript), and the timeout means the same in both:
the total time from sending the request to reading the last byte of the
response, ending in `ClientTimeoutError`. Requests governed by another budget
are not cut by it: waits and long polls use their own deadline, streams
(process logs, file watches, build logs, events) are bounded only until their
response headers arrive, and file transfers (uploads, downloads and the bulk
read / write calls) are not bounded. `0` disables it, and so does a value
above 2,147,483 seconds (about 24.8 days, the longest timer JavaScript can
arm); a negative or non-finite value is a `BadRequestError`. The events hub
durations (`reconcile_interval` / `idle_close`, TypeScript
`reconcileIntervalSeconds` / `idleCloseSeconds`) are validated the same way. TypeScript: the root option
`requestTimeout` (milliseconds) is renamed `requestTimeoutSeconds` (seconds);
Python keeps `request_timeout` (seconds).
