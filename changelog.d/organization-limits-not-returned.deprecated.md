**Some organization limits are not returned** — Daytona determines a few
organization limits for each organization and no longer returns them:
`sandboxes.total` and the `storage` counts (`volumes`, `images`, `snapshots`)
of `get_limits()` / `getLimits()` still carry their `usage`, but their `limit`
is always `None` / `undefined`, and those fields are now deprecated in both
SDKs. A create that reaches one of these limits raises `LimitExceededError`
naming the limit, and its `current` / `max` may be `None` / `undefined`;
contact support to request a higher limit. Likewise `ImageTooLargeError` and
`SnapshotTooLargeError` may carry no `max_bytes` / `bytes` (`maxBytes` /
`bytes` in TypeScript).
