**`get()`'s connect options are checked** — `toolbox_url` / `toolboxUrl`, when
given, is a non-blank string, and `access_token` / `accessToken`, sent as a
bearer credential, follows the header rule, both before the request, with
`BadRequestError` naming the option (Python raised a raw `AttributeError`
after the request for a non-string URL, and TypeScript a raw `TypeError` and
stored a non-string token). A TypeScript `null` for either is omitted, as
Python's `None` is, so the handle mints its own sandbox token (TypeScript took
`accessToken: null` as a caller token and never minted).
