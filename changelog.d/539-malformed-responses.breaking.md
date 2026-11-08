**Responses that break the contract are `BadRequestError`** — the responses of
the client, sandbox lifecycle and platform services are checked against the
contract fields the SDK reads, nested fields and list items included: images,
snapshots, volumes, secrets, reservations (and bulk reservations), Mac models
and the catalog, the account responses, ports' preview endpoints and links,
sandbox access tokens, the sandbox body, and list and search pages. A missing
required field or a wrong type is a `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`) naming the field, before any further request: `images.build()` /
`pull()`, `capture_image()` / `captureImage()`, `create_snapshot()` /
`createSnapshot()` and `fork()` check the id they follow. TypeScript used to
throw a raw `TypeError`, build a model with missing fields, or request
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
without a bound. A required field the contract allows to be null (for example
the project budget's `monthly_limit_cents`, `period_start_date`, `created_at`
and `updated_at`) must be present, while an explicit `null` stays valid.
Python no longer requires the per-item `cursor` of a list page; neither SDK
reads it.
