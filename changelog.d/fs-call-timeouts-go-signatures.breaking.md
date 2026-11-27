**Go `FileSystem.Grep`, `Glob` and `Replace` signatures** — each takes its
per-call options as a variadic last parameter (`...*daytona.FileSearchOptions`
for `Grep` / `Glob`, `...*daytona.ReplaceOptions` for `Replace`), so existing
calls compile unchanged, but a method value or an interface typed with the old
`Grep(context.Context, string, string)`, `Glob(context.Context, string, string)`
or `Replace(context.Context, []string, string, string)` signature must be
updated.
