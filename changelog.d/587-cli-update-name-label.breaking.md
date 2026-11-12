**CLI: `sandbox update --label` keeps the name label** — replacing the label set
(`--label`, `--clear-labels`, `--file`) keeps the label `name` a named create
stamped, so SDK `find_by_name` and label searches keep finding the sandbox; a
new set that names it must use the sandbox's name.
