**Iterator errors say where to resume** — the error that ends an `iter()` walk
(Go: `ListAll` / `SearchAll` / `ListEndpointsAll`) carries the cursor of the
page that failed (`page_cursor` / `pageCursor` / `PageCursor`; empty for the
first page). Passing it as the cursor of the same call resumes the walk with
that page instead of from the beginning.
