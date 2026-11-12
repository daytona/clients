**Lists read search first, and are no longer read-your-writes where search is served** — the
client's sandbox `list()` / `iter()`, `images.list()` / `iter()` and `snapshots.list()` / `iter()`
in both SDKs, and the CLI's `sandbox list`, `image list` and `snapshot list`, now read the
project-wide search routes (`GET /sandboxes/search`, `GET /images/search`,
`GET /snapshots/search`), which span every region, and fall back to the plain list routes only
where the deployment does not serve search (501 `SEARCH_NOT_CONFIGURED`, or 404 from an API
without the route). A deployment that serves its lists from search answers the plain list routes
with 410 `GONE`, so lists keep working on both kinds of deployment. On a deployment that serves
search, these lists and commands are no longer read-your-writes: search is eventually
consistent, so results can trail a create, update or delete by a few seconds; use `get()` (or
`daytona sandbox|image|snapshot get`) for a fresh read. A client remembers a deployment without
search, so later list calls and pages go straight to the list route. A 503 `SEARCH_UNAVAILABLE`
is raised as before, without a fallback. `images.get('search')` and `snapshots.get('search')`
(and `daytona image|snapshot get search`) now fail before any request, since `search` names the
search route: read such an image or snapshot by its ID. Method signatures are unchanged.
