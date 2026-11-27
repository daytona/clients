**CLI: shell completion offers flag values and profile names** — `-o`,
`sandbox wait --for`, `volume create --type`, `image list --platform`,
`sandbox process list --state` / `--kind`, the timeout actions, `config get` /
`set`, signal names and the other fixed vocabularies complete their values,
and `--profile`, `profile use` and `profile delete` complete the saved
profile names. Completion sends no request, and an argument that names a
remote resource no longer falls back to local file names (`sandbox cp` still
completes local paths).
