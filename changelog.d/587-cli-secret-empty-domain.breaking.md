**CLI: Refuse empty secret domain entries** — `secret set --allowed-domain`
rejects empty entries with exit 2 instead of silently preserving the old scope.
Use `--allowed-domain '*'` to remove domain restrictions explicitly.
