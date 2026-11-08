**CLI: `image logs --follow -o json` streams NDJSON** — one `{"data": "..."}`
object per chunk, like `sandbox logs --follow`; `--follow` with YAML output is
refused before any request.
