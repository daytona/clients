**Names sent in a request body are checked** — the name `images.promote()` and
`create_snapshot()` / `createSnapshot()` send in the request body must be a
string that is not blank, in both SDKs, before any request, with
`BadRequestError` naming it (both sent it as given).
