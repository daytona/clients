**Responses that break the contract are `BadRequestError`** — every response
the client, lifecycle and platform services map is checked against the
contract fields the SDK reads, nested fields and list items included: images,
snapshots, secrets, volumes, reservations (and bulk reservations), Mac models
and the catalog, the account responses, preview endpoints and links, sandbox
access tokens, the sandbox body and search pages. A missing required field or
a wrong type is a `BadRequestError` (400, `BAD_REQUEST`, source `SDK`) naming
the field, before any further request: `images.build()` / `pull()`,
`capture_image()` / `captureImage()`, `create_snapshot()` /
`createSnapshot()` and `fork()` check the id they follow. TypeScript used to
throw a raw `TypeError`, build a model with missing fields, or request
`/images/undefined`; Python `images.get()` raised a raw `KeyError`. An access
token whose `expires_at` does not parse, a malformed access-token response and
a non-text entrypoint log are `BadRequestError` too (they were 500 with no
code), and a search page needs its `total`, `total_relation` and a string
`next_cursor` (a mistyped cursor used to end `search()` / `find_by_name()`
early). A detail read behind a list item that breaks the contract leaves the
list item, as an unreadable one does.
