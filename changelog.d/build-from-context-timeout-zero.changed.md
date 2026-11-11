**Python `build_from_context(timeout=0)` means no limit** — as every other 2.x
timeout and TypeScript's `timeoutSeconds: 0`: the build request that follows the
upload then has no deadline. It used to fall back to the client's request
timeout.
