**`daytona org limits` table** — the table no longer has `Total sandboxes`,
`Images`, `Snapshots` and `Volumes` rows, because the API does not return
those limits; `-o json` and `-o yaml` still print the whole response,
including their usage.
