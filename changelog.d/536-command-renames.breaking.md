**CLI: command renames** — `invite show` → `invite get`; `sandbox preview-url`
→ `sandbox port preview-url`; `sandbox preview-url expire` → `sandbox port
preview-url expire`. The `image import` alias is removed (use `image
create`). The old names are refused with a hint naming the new command, and
the old lifecycle `--timeout SECONDS` with a hint naming
`--lifecycle-timeout`.
