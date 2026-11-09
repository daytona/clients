**CLI: `secret set` refuses an empty value** — an empty `--value`, empty stdin,
an empty file or `--from-file ""` is a usage error instead of overwriting the
stored value.
