**A create handle's labels are unknown, not empty** — a handle that holds only
what a create or fork response carries (`wait_seconds=0` / `waitSeconds: 0`,
or a wait that ended without reading the sandbox) has no labels from it:
`sandbox.labels` is now `None` / `undefined` (unknown) instead of an empty
mapping that read as "no labels", until a detail read (`refresh_data()` /
`refreshData()`, or a wait that reads the sandbox) or `set_labels()` /
`setLabels()` reports them. `to_json()` / `toJSON()` write `labels` whenever
they are known, an empty set included.
