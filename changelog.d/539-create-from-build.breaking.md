**`create_from_build()` bounds only the build wait** — `create_from_build()` /
`createFromBuild()` bounds the image build wait by its own `build_timeout` /
`buildTimeoutSeconds` (unbounded by default), starting once the build
submission (an ordinary request, bounded by the request timeout) is accepted.
When that budget runs out, it raises `RequestTimeoutError` (408,
`REQUEST_TIMEOUT`, source `SDK`) before any sandbox exists; a server 408
during the build wait keeps the server's error in both SDKs (TypeScript used
to report it as the SDK's build timeout). An image that ends in `failed` or
`error` raises `ImageBuildFailedError`. The create then gets the full
`wait_seconds` / `waitSeconds`.
