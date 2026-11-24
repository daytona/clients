**Client configuration checked before any request** — Python's root
`Daytona(...)` / `AsyncDaytona(...)` refuse a config that is not a
`DaytonaConfig`, and both Python clients refuse an `events` value that is not a
`SandboxEventsConfig`, with a `BadRequestError` naming it instead of a raw
`TypeError` / `AttributeError`. `shared_hub` / `sharedHub`, `otel_enabled` /
`otelEnabled` and `events.enabled` must be booleans (a string such as `"no"`
was read as true). `get()`'s `toolbox_url` / `toolboxUrl` override must be an
absolute `http` / `https` URL with a host and no query or fragment, refused
before the sandbox is read rather than on the first toolbox call, as the Go
SDK already does.
