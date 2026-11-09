**`MacReservation.sandboxes_per_mac` removed** — the API no longer reports how
many sandboxes a reservation's Mac runs, so Python
`MacReservation.sandboxes_per_mac` and TypeScript
`MacReservation.sandboxesPerMac` are gone, and the `daytona reservation`
commands' table output no longer has a `sandboxes_per_mac` row. A model's limit is its
`max_sandboxes` / `maxSandboxes` (`mac_models.list()` / `macModels.list()`,
`daytona mac-model list`).
