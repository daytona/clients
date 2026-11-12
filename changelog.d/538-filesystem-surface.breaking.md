**Filesystem defaults and removed aliases** — `mkdir()` defaults to mode
`"0755"` in both SDKs. TypeScript `readText()` decodes strictly as UTF-8 and
keeps a leading byte-order mark, like Python; `WriteFileOptions.mode` is
removed (use `chmod()`), as are the aliases `Match`, `SearchFilesResponse`,
`FilePermissionsParams` and `ListBranchResponse`.
