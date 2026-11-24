**Python config type checks** — the root `Daytona(...)` / `AsyncDaytona(...)`
refuse a config that is not a `DaytonaConfig`, and both Python clients refuse
an `events` value that is not a `SandboxEventsConfig`, with a
`BadRequestError` naming it instead of a raw `TypeError` / `AttributeError`. A
0.x-shaped mapping with `api_url`, `server_url` or `toolbox_url` keeps the
migration `TypeError` that names the field.
