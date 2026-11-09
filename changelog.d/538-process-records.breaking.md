**Checked process records and lists** — the record `start()`, `run()`,
`exec()` and `create_session()` / `createSession()` get back from the sandbox
is checked in full in both SDKs (TypeScript checked only its `id`, so a record
without `state` read as running), and `process.list()` accepts only the API's
`{ items }` response: a missing or non-list `items` raises `BadRequestError`
(400, `BAD_REQUEST`, source `SDK`) instead of an empty page, and the page
carries no `next_cursor` / `nextCursor`, which the API does not send. A
process record's `pid` and `exitCode`, and a wait result's `exitCode`, must be
signed 32-bit integers, as the contract types them: one outside that range is
`BadRequestError` naming the field.
