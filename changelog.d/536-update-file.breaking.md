**CLI: `sandbox update --file` is strict** — the body accepts only `labels` and
`lifecycle_policy`; any other key, or a `lifecycle_policy` that is not an
object, is a usage error instead of being dropped.
