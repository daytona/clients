**CLI: every sandbox create sends an idempotency key** — `sandbox create*`
without `--idempotency-key` or `--name` now sends a fresh key per invocation,
as `reservation create` and the SDKs do, and a create that fails without an
answer (network error, 5xx, timeout) names the key to retry with in its hint.
A blank or malformed `--idempotency-key` is a usage error.
