**Volumes: Python `Volume.options` is a plain `dict`** owned by the volume
(`Mapping` to type checkers), so `dataclasses.asdict`, `copy.deepcopy`,
`pickle` and `json.dumps` work on it.
