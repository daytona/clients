**`set_labels()` keeps the `name` label** — `sandbox.set_labels()` /
`setLabels()` replaced every label, so a call without `name` removed the
reserved `labels["name"]` and `find_by_name()` / `findByName()` stopped
finding the sandbox. When the new labels do not set `name`, the SDKs now read
the sandbox first and send its current `name` label along; passing `name`
still replaces it.
