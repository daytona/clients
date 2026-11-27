**CLI: `--file` errors name the JSON problem** — a `--file` that holds a JSON
array or another non-object value says so (`--file body.json must contain a
JSON object, got an array`), and a request field of the wrong type is named
in JSON terms (`field "cpu" cannot be a string`, `field "env.B" cannot be a
number`, `field "disk_mib" must be a whole number, got 1.5`, or out of range)
instead of a Go decoder error.
