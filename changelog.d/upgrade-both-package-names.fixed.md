**Migration tool (TypeScript): both package names** — a project with both
`@daytonaio/sdk` and `@daytona/sdk` leaves every old-name entry (dependency
and `resolutions` / `overrides` alike) for manual reconciliation, so the two
never name different packages.
