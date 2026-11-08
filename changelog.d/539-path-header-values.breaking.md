**Path, body and header values are checked** — in both SDKs, before any
request, with `BadRequestError` naming the argument: every id or name a call
puts into a request path (images, snapshots, volumes, secrets, reservations,
the organization id of `get_limits()` / `getLimits()`, the id `get()` and the
client lifecycle verbs take) must be a string that is not blank and has no
lone surrogate (TypeScript sent `/secrets/undefined`, Python raised a raw
`TypeError`, and neither refused a blank id); the name `images.promote()` and
`create_snapshot()` / `createSnapshot()` send in the request body must be a
string that is not blank; `sandbox.volumes.pull()` takes a volume id and a
request id of 1 to 128 ASCII letters, digits, underscores or hyphens; the
idempotency key (`idempotency_key` / `idempotencyKey`, sent as
`Idempotency-Key` on create and the reservation calls) and the ETag
(`if_match` / `ifMatch`, sent as `If-Match`) follow the header rule with no
exception: non-empty printable ASCII with no leading or trailing whitespace,
never stripped, since an idempotency key or ETag the SDK altered would not be
the caller's value. `region` is the one exception, on `create*()`,
`create_from_build()` / `createFromBuild()`, `find_by_name()` / `findByName()`
and `images.build()` / `pull()`: a blank or whitespace-only value, or `local`
in any letter case and with any surrounding whitespace, selects the default
region and is not sent, neither as the `x-daytona-region` header nor as the
body's `region_id` (the image calls sent a blank value or `local` as
`region_id`); any other value there is sent as given and follows the header
rule. The reservation calls send `region` as given, because their contract
reads a blank value or `local` as no region. Both SDKs refuse the same values,
so neither sends header bytes the other would not and neither fails with a
transport error (Python reported a padded value as a `ConnectionError`, or for
a create as `SandboxCreateConnectionError`, TypeScript sent it, and Python's
`find_by_name()` sent a blank region as an empty `x-daytona-region` header).
`get()`'s connect options are checked too: `toolbox_url` / `toolboxUrl`, when
given, is a non-blank string, and `access_token` / `accessToken`, sent as a
bearer credential, follows the header rule (Python raised a raw
`AttributeError` after the request for a non-string URL, and TypeScript a raw
`TypeError` and stored a non-string token). A TypeScript `null` for either is
omitted, as Python's `None` is, so the handle mints its own sandbox token
(TypeScript took `accessToken: null` as a caller token and never minted).
