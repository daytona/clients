**Migration tool (TypeScript): config and manifest output** — with
`--drop-api-url`, an API URL the tool cannot verify is reported as asserted by
that flag, no longer asking to rerun with it; removing a Daytona Cloud `apiUrl`
leaves no doubled space; a project with both Daytona package names leaves every
old-name entry (dependency and `resolutions` / `overrides` alike) for manual
reconciliation, so the two never name different packages; and a file passed as
the project path is refused with a message naming the directory to pass, a
missing one with `Path does not exist`, as the Python tool says.
