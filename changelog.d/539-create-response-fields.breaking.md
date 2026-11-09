**Create and fork responses need only an `id`** — only the `id` of a create or
fork response is required, in both SDKs, and it must be one a request path can
carry (not empty or blank, `.` or `..`, nor a surrogate the SDK's path-id rule
refuses); without one the call raises `BadRequestError`, before any further
request. A `status` that is missing or not a string gives state `unknown`, a
fork's `forked_from` falls back to the source sandbox's id (also when the
fork's detail read omits it), and any other field that is missing or malformed
(a toolbox URL, a name, a token that could not be sent as a header, a
date-time that is not an RFC 3339 timestamp, a reservation id) is absent, the
toolbox URL then coming from the detail read a wait makes or from
`refresh_data()` / `refreshData()`. Before, TypeScript threw a raw `TypeError`
for a non-string create toolbox URL, both SDKs refused a fork response without
`forked_from` or a toolbox URL, and an empty, blank, `.` or `..` id was
accepted. A create response's `access_token_expires_at` that is not an RFC
3339 timestamp reads as absent and the token is used until a 401, as does the
expiry of a token the handle mints itself.
