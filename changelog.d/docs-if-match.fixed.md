**If-Match on network policy and secrets updates** — a stale ETag raises `PreconditionFailedError` (412) and a malformed one `BadRequestError` (400).
