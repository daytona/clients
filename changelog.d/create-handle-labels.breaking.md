**A create handle's labels are unknown, not empty** — a handle returned by
`create*()` or `fork()` holds only what the create response carries, which has
no labels: `sandbox.labels` is now `None` / `undefined` (unknown) instead of an
empty mapping that read as "no labels", until `refresh_data()` /
`refreshData()` or `set_labels()` / `setLabels()` reports them.
`to_json()` / `toJSON()` write `labels` whenever they are known, an empty set
included.
