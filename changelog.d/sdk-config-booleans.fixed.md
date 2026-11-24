**Boolean config options must be booleans** — `shared_hub` / `sharedHub`,
`otel_enabled` / `otelEnabled` and `events.enabled` raise `BadRequestError`
for any other value; a string such as `"no"` was read as true. In Python,
`shared_hub` and `events.enabled` (typed `bool`) refuse `None` too, which read
as false; `otel_enabled=None` still reads the environment.
