**Timestamps are strings in both SDKs** — Python now refuses a number where
the contract has a date or timestamp string in the responses it checks (the
sandbox and its lifecycle policy, images, snapshots, preview endpoints, signed
preview links and the project budget) with `BadRequestError` naming the field,
as TypeScript already did; Python's generated models read a number as a Unix
time. A build-log state event whose `deadline_at` is not a string is the
build-log protocol error (`BUILD_LOG_PROTOCOL_ERROR`), as every malformed
build-log event is, in both SDKs. A create response's
`access_token_expires_at` that is not a timestamp is not refused but read as
absent, as every optional create-response field is once the sandbox exists:
the token is then used until a 401.
