**Ports: a `.` or `..` sandbox id is refused locally** — every `ports.*` call
that reaches the API refuses a sandbox id of `.` or `..` with `BadRequestError`
(400, `BAD_REQUEST`, source `SDK`) before any request, in both SDKs, instead of
sending a path a URL resolver would collapse onto another route.
