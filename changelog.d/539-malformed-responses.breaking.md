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
code), and a search page needs its `total`, `total_relation` and a string
`next_cursor` (a mistyped cursor used to end `search()` / `find_by_name()`
early); `list()` checks `nextCursor` as it checks `next_cursor`. Python
refuses a sandbox object whose `labels` is not a map of strings, as TypeScript
does (it dropped them silently). A detail read behind a list item that breaks
the contract leaves the list item, as an unreadable one does. A response or
events frame nested deeper than the JSON decoder can follow is a malformed
response naming the response (Python) or skipped (an events frame), never a
raw `RecursionError`, and nothing the SDK does with response data recurses
without a bound. An integer too large for a double, which overflows to
infinity (`10**400`; Python read it, TypeScript's `JSON.parse` turns it into
`Infinity`), is refused in both SDKs; an integer above 2^53 within a double's
range (2^53 + 1, a search `total` of 2^60) is accepted in both, and TypeScript
may round it. Both SDKs refuse an image whose `platform`, `default` or
`deprecated` is missing or not a boolean, and a sandbox whose `region_id` is
missing or `null`, with `BadRequestError` naming the field, as the contract
requires them (released versions read a missing flag as `false` and derived a
missing `region_id` from the sandbox id); only an empty `region_id` is derived
from the id. A required field the contract allows to be null (for example the
project budget's `monthly_limit_cents`, `period_start_date`, `created_at` and
`updated_at`) must be present, while an explicit `null` stays valid. Python no
longer requires the per-item `cursor` of a list page; neither SDK reads it. A
search page's `total` must be a non-negative integer (the contract's minimum
of 0; both SDKs read a negative one as given). A sandbox's `lifecycle_policy`
is checked field by field in TypeScript as Python's model checks it: integer
`timeout_seconds` / `idle_expiry_seconds`, string actions (an unknown one is
kept), string deadlines and activity source (TypeScript read a malformed field
as absent). Python also refuses a number where the contract has a timestamp
string, on the sandbox and its lifecycle policy (its model read it as a Unix
time).
