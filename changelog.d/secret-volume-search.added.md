**Secret and volume search** — the Python and TypeScript SDKs add
`secrets.search(...)` and `volumes.search(...)`, and the Go SDK adds
`Secrets().Search` and `Volumes().Search` (`SearchSecretsOptions`,
`SearchVolumesOptions`, returning a `SearchPage`). Both take server-side
text, name, name-prefix, created and updated bounds, sort, order, limit and
cursor; secret search adds an allowed-domain filter, and volume search adds
type, status, imported and last-used filters.
