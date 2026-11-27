**CLI: process tags and names** — `sandbox exec --tag` and `--name` start a
tagged or named process, `sandbox process list --tag` filters by tag, and
`sandbox process kill-all <sandbox> --tag T [--timeout D]` kills every
process started with the tag, and everything they started, then prints the
ids it stopped. A tag that is not 1-128 characters of `[A-Za-z0-9._-]` (or is
`.` / `..`) is a usage error before any request.
