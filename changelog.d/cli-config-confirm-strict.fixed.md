**CLI: `config set confirm` takes only `true` or `false`** — any other value,
such as `1`, `t` or `TRUE`, is a usage error (exit 2) and leaves the setting
unchanged. Before, every spelling Go's `strconv.ParseBool` accepts was saved,
although the help and the usage error list only `true|false`.
