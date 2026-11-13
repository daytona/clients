**CLI member dry runs** — `daytona member update --dry-run` and
`daytona member remove --dry-run` no longer print `revoked_preview_grants`:
the updated API contract no longer reports it, and the JSON output carries
only `revoked_api_keys` (with `user_id`).
