**Configuration and environment** — SDK 2.x reads its API endpoint only from
`DAYTONA_V2_API_URL` (test environments only); `DAYTONA_API_URL` belongs to
the 0.x SDK and is no longer read by anything 2.x. Ambient values
(`DAYTONA_API_KEY`, `DAYTONA_JWT_TOKEN`, `DAYTONA_PROJECT_ID`,
`DAYTONA_V2_API_URL`) are trimmed in both SDKs and a blank one counts as
unset; explicit config values are used as given, and an explicitly empty
`project_id` / `projectId` (or an empty `jwt_token` without an API key) is a
`BadRequestError` (400, `BAD_REQUEST`, source `SDK`) in both. Python
`Daytona(config)` / `AsyncDaytona(config)` now copy the config when they are
built, like TypeScript: changing the object afterwards has no effect (a URL
field on it is refused at construction). TypeScript configuration errors now
carry status 400, code `BAD_REQUEST` and source `SDK`. The deprecated root
`target` stays, deprecated, for all of 2.x.
