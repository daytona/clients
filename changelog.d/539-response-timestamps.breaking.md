**Every date-time field is an RFC 3339 timestamp** — both SDKs check every
field the contract types as a date-time, in every response they map, with one
RFC 3339 rule (ASCII digits, a real calendar date and clock time joined by `T`
or `t`, no leap second, an offset of `Z` / `z` or `±hh:mm`, an optional
fraction of a second): the sandbox (`created_at`, `updated_at`, `started_at`,
`stopped_at`, `last_activity_at`, `create_timeout_at`, `spot_evicted_at` and
its lifecycle policy's `timeout_at`, `idle_timeout_at` and `last_activity_at`)
on `get()`, refresh, list and search and in the lifecycle-policy update and
idle-timer reset echoes; images (`created_at`, `updated_at`, `expires_at`);
snapshots; volumes (`created_at`, `updated_at`, `last_used_at`,
`used_bytes_at`); secrets; preview endpoints; signed preview links'
`expires_at`; reservations (`created_at`, `activated_at`,
`pending_mac_started_at`, `period_started_at`, `period_end`, `reissued_at`,
`ended_at`); the current API key; and the project budget. A number, or a
string that is not an RFC 3339 timestamp (a bare date, a date-time without an
offset), is a `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the
field. Before, TypeScript accepted any string, and Python read a number as a
Unix time and a date or a date-time without an offset as UTC (a signed preview
link's `expires_at` without an offset included). The project budget's
`period_start_date` stays a date. A create or fork response is the exception,
as for its other optional fields: a malformed date-time there reads as absent,
and a read during the create or fork wait that breaks the contract is a failed
read, so the handle is returned; a create response's `access_token_expires_at`
that is not an RFC 3339 timestamp is likewise read as absent, and the token is
then used until a 401. A build-log state event whose `deadline_at` is not a
string is the build-log protocol error (`BUILD_LOG_PROTOCOL_ERROR`), as every
malformed build-log event is, in both SDKs.
