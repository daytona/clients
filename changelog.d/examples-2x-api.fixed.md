**Examples run on the 2.x API and fail on a failed check** — the Python
examples call `create()` / `create_from_build()` with the parameters the SDK
accepts (`wait_seconds`, an image name or `ImageBuilder`), and the TypeScript
examples use `waitSeconds`. Every example now exits non-zero when one of its
checks or commands fails, the secrets examples use a run-scoped secret they
never overwrite and always clean up, and `examples/run-examples.sh check`
type-checks the examples and runs offline tests against a fake client.
