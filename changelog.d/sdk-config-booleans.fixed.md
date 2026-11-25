**Boolean config options must be booleans** — `shared_hub` / `sharedHub`,
`otel_enabled` / `otelEnabled` and `events.enabled` raise `BadRequestError`
for a value of another type; a string such as `"no"` was read as true. Python
`None` and TypeScript `undefined` / `null` are accepted as before.
