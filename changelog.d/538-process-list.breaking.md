**`process.list()` reads only the `{ items }` envelope** — in both SDKs a
missing or non-list `items` raises `BadRequestError` (400, `BAD_REQUEST`,
source `SDK`) instead of an empty page, and the page carries no `next_cursor`
/ `nextCursor`, which the API does not send.
