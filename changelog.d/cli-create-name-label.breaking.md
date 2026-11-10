**CLI: named creates match the SDKs** — `sandbox create*` with `--name` stamps
the label `name` (so the SDKs' `find_by_name` finds CLI-made sandboxes) and
derives its idempotency key from the name as the SDKs do, so creating the same
name again replays the first create; `--idempotency-key` overrides it. A
`--label name=…` that disagrees with `--name`, and a blank `--name`, are usage
errors.
