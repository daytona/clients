**Path ids and names are checked** — in both SDKs, before any request, with
`BadRequestError` naming the argument: every id or name a client, lifecycle or
platform-service call puts into a request path (images, snapshots, volumes,
secrets, reservations, the organization id of `get_limits()` / `getLimits()`,
the id `get()` and the client lifecycle verbs take) must be a string ("must be
a string") that is not blank ("must be a non-empty string"), has no surrogate
code point and is not `.` or `..`: both SDKs refuse surrogate code points in
path ids; in TypeScript that means unpaired UTF-16 surrogates, since a paired
one is a real astral character, which both accept. A dot segment is resolved
away by the HTTP client, so `secrets.delete("..")` reached another route.
Before, TypeScript sent `/secrets/undefined`, Python raised a raw `TypeError`,
and neither refused a blank id. One check covers every id in a request path in
both SDKs (Python `path_segment`, TypeScript `pathSegment`), so a toolbox id
(a process, session, interpreter context or recording id) that is blank is
refused too, with the same messages.
