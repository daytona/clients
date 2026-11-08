**CLI: `--file` refuses keys that differ only in case** — a body with both
`cores_max` and `CORES_MAX` (at any depth) is a usage error; which spelling
won used to depend on map order.
