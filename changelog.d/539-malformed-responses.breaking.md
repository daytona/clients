**Responses that break the contract are `BadRequestError`** — the responses of
the client, sandbox lifecycle and platform services are checked against the
contract fields the SDK reads, nested fields and list items included: images,
snapshots, volumes, secrets, reservations (and bulk reservations), Mac models
and the catalog, the account responses, ports' preview endpoints and links,
the Docker status, a volume pull, port-in-use, system metrics, the
lifecycle-policy update and idle-timer reset, sandbox access tokens, the
sandbox body, and list and search pages. A missing required field or a wrong
type is a `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming the
field, before any further request: `images.build()` / `pull()`,
`capture_image()` / `captureImage()`, `create_snapshot()` / `createSnapshot()`
and `fork()` check the id they follow. TypeScript used to throw a raw
`TypeError`, build a model with missing fields, or request
`/images/undefined`; Python `images.get()` raised a raw `KeyError`. An access
token whose `expires_at` does not parse, a malformed access-token response and
a non-text entrypoint log are `BadRequestError` too (they were 500 with no
code). Python refuses a sandbox object whose `labels` is not a map of strings,
as TypeScript does (it dropped them silently). A detail read behind a list
item that breaks the contract keeps the list item, as an unreadable one does.
Both SDKs refuse a sandbox whose `id` or `class` is empty or blank (TypeScript
refused only an empty one; Python accepted both), and an acknowledgement of
`images.build()` / `pull()`, `capture_image()` / `captureImage()` or
`create_snapshot()` / `createSnapshot()` whose `id` is empty or blank, `.` or
`..`, before the read that follows it (it requested `/images/` or
`/snapshots/`, or the collection one level up). Both SDKs refuse an image
whose `platform`, `default` or `deprecated` is missing or not a boolean, and a
sandbox whose `region_id` is missing or `null`, with `BadRequestError` naming
the field, as the contract requires them (released versions read a missing
flag as `false` and derived a missing `region_id` from the sandbox id); only
an empty `region_id` is derived from the id. A required field the contract
allows to be null (for example the project budget's `monthly_limit_cents`,
`period_start_date`, `created_at` and `updated_at`) must be present, while an
explicit `null` stays valid. A sandbox's `lifecycle_policy` is checked field
by field in TypeScript as Python's model checks it: integer `timeout_seconds`
/ `idle_expiry_seconds`, string actions (an unknown one is kept), a string
activity source, and deadlines that are RFC 3339 timestamps, as are a
sandbox's `spot_evicted_at`, `last_activity_at` and `create_timeout_at` (read
as absent in a create or fork response, as its other fields are) and a
reservation's `created_at`, `activated_at`, `pending_mac_started_at`,
`period_started_at`, `period_end`, `reissued_at` and `ended_at` (TypeScript
read a malformed field as absent; Python read a date, or a date-time without
an offset, as UTC, and the idle-timer reset and policy update echoes read an
unparseable activity clock as absent). A create or fork response is the
exception: once its `id` came back the sandbox exists, so the call returns the
handle (see the `create*()` and `fork()` entry).
