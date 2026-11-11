**CLI: `image logs` without `--follow` reads at most 64 MiB** — a larger log is
an error that suggests `--follow`, which streams it, instead of buffering the
whole log in memory.
