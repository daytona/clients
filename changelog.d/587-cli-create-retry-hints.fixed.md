**CLI: safer create retries** — an interrupted `sandbox create*` also names the
idempotency key to retry with, a create answer whose sandbox id is not a usable
path segment is refused, and `--file` `wait_seconds` with `--no-wait` is a usage
error like `--server-wait`.
