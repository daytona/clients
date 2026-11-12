**SDK lists read search first, and are no longer read-your-writes where search is served** —
the client's sandbox `list()` / `iter()`, `images.list()` / `iter()` and `snapshots.list()` /
`iter()` in both SDKs now read the project-wide search routes (`GET /sandboxes/search`,
`GET /images/search`, `GET /snapshots/search`), which span every region, and fall back to the
plain list routes only where the deployment does not serve search (501 `SEARCH_NOT_CONFIGURED`,
or 404 from an API without the route); a deployment that serves its lists from search answers
the plain list routes with 410 `GONE`. Where search is served these lists are eventually
consistent, so results can trail a create, update or delete; `get()` is the fresh read. A
client remembers a deployment without search, so later calls and pages go straight to the list
route, until that route answers 410 `GONE`: the client then tries search again. A 503
`SEARCH_UNAVAILABLE` is raised, without a fallback. Method signatures are unchanged.
