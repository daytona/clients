**Organization limits that are not returned** — the API no longer returns the
organization limits on total sandboxes, images, snapshots and volumes: their
`limit` is null although the limits still apply (a create that reaches one
fails with `LIMIT_EXCEEDED`). Both SDKs document that a null / undefined
`limit` on `sandboxes.total` and the `storage` counts does not mean unlimited.
