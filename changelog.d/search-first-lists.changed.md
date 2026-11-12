**Lists read search first** — the client's sandbox `list()` / `iter()`, `images.list()` / `iter()`
and `snapshots.list()` / `iter()` in both SDKs, and the CLI's `sandbox list`, `image list` and
`snapshot list`, now read the project-wide search routes (`GET /sandboxes/search`,
`GET /images/search`, `GET /snapshots/search`), which span every region, and fall back to the
plain list routes only where the deployment does not serve search (501 `SEARCH_NOT_CONFIGURED`,
or 404 from an API without the route). A deployment that serves its lists from search answers
the plain list routes with 410 `GONE`, so lists keep working on both kinds of deployment. Where
search serves a list it is eventually consistent: an item created a few seconds ago may be
missing; `get()` reads it at once. A 503 `SEARCH_UNAVAILABLE` is raised as before, without a
fallback. Method signatures are unchanged.
