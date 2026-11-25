**Iterators retry a failed page and say where to resume** — every `iter()`
(Python and TypeScript, plus `iter_endpoints()` / `iterEndpoints()`) and every
Go `ListAll` / `SearchAll` / `ListEndpointsAll` reads a page again after a
dropped connection or an HTTP 429, 502, 503 or 504, up to three reads of that
page, waiting at least the server's retry hint (a hint above 10 seconds is not
waited for). The error that ends a walk carries the cursor of the page that
failed (`page_cursor` / `pageCursor` / `PageCursor`; empty for the first
page): passing it as the cursor of the same call resumes the walk with that
page instead of from the beginning.
