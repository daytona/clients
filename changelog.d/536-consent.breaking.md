**CLI consent** — `config set confirm false` turns off prompts at a terminal
only; off a terminal destructive and billable commands still need `--yes` or
`DAYTONA_YES=1`. `billing automatic-top-up delete`, `billing
balance-notification delete` and `reservation renew` now ask for that consent.
`secret set --from-file ""` is a usage error instead of storing an empty value,
and `sandbox update --file` accepts only `labels` and `lifecycle_policy` and
refuses lifecycle actions the sandbox cannot run (`delete`; `stop` on a
persistent macOS sandbox) before any change.
