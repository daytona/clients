**Some organization limits are not returned** — Daytona determines a few
organization limits for each organization and no longer returns them:
`sandboxes.total` and the `storage` counts (`volumes`, `images`, `snapshots`)
of `get_limits()` / `getLimits()` still carry their `usage`, but their `limit`
is always `None` / `undefined`, and those fields are now deprecated in both
SDKs. A create that reaches one of these limits is refused with
`LIMIT_EXCEEDED` naming the limit and carrying no `current` / `max`; contact
support to request a higher limit. The `daytona org limits` table drops its
`Total sandboxes`, `Images`, `Snapshots` and `Volumes` rows, while `-o json`
and `-o yaml` keep printing the whole response.
