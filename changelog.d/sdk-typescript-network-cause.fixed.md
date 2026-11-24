**TypeScript `ConnectionError` keeps its network cause** — `cause` is the
runtime's network error (`ECONNREFUSED`, `ENOTFOUND`, fetch's `TypeError`,
...), never the HTTP client's error with its request headers.
