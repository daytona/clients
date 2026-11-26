**Secret and volume search** — the Python and TypeScript SDKs add
`secrets.search(...)` and `volumes.search(...)`, and the Go SDK adds
`Secrets().Search` and `Volumes().Search` (`SearchSecretsOptions`,
`SearchVolumesOptions`, returning a `SearchPage`), with server-side text,
name, timestamp, type, status, imported, sort, order, limit and cursor
filters supported by each resource.
