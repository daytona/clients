**CLI: `--file` refuses two spellings of one field** — a body with keys that
name the same request field and differ only in case (`cores_max` and
`CORES_MAX`, also inside `lifecycle_policy`, `network_policy`, `volumes` and
other nested objects) is a usage error; one of the values used to be dropped
silently. Keys of map values such as `env` and `labels` are names of your own
and are sent as given (`PATH` and `path` are two variables).
