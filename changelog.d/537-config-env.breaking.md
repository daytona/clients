**Configuration and environment** — SDK 2.x reads its API endpoint only from
`DAYTONA_V2_API_URL` (a regional endpoint or a test environment);
`DAYTONA_API_URL` belongs to the 0.x SDK and is no longer read by anything
2.x. Ambient values (`DAYTONA_API_KEY`, `DAYTONA_JWT_TOKEN`,
`DAYTONA_PROJECT_ID`, `DAYTONA_V2_API_URL`) are trimmed in both SDKs and a
blank one counts as unset. Explicit config values are used as given, and an
explicitly empty or non-string one (`api_key` / `apiKey`, `jwt_token` /
`jwtToken`, `project_id` / `projectId`) is a `BadRequestError` (400, `BAD_REQUEST`, source
`SDK`) in both, never a reason to read the environment. Missing credentials
raise `AuthenticationError` with status 401, code `UNAUTHORIZED` and source
`SDK`. Python `Daytona(config)` / `AsyncDaytona(config)` now copy the config
when they are built, like TypeScript: changing the object afterwards has no
effect (a URL field on it is refused at construction). TypeScript copies the
nested `events` object too, and its configuration errors, including the
refused `apiUrl` / `serverUrl` / `toolboxUrl` keys (a `TypeError` before),
are `BadRequestError`s. The deprecated root `target` stays, deprecated, for
all of 2.x.
